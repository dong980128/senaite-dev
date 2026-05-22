# -*- coding: utf-8 -*-
#
# This file is part of SENAITE.CORE.
#
# SENAITE.CORE is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, version 2.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc., 51
# Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# Copyright 2018-2025 by it's authors.
# Some rights reserved, see README and LICENSE.

import logging
import json
import os

from bika.lims import api
from bika.lims import senaiteMessageFactory as _
from Products.Five.browser.pagetemplatefile import ViewPageTemplateFile
from senaite.core.browser.modals import Modal
from six import string_types
from bika.lims.api.user import get_allowed_keywords
from Products.CMFCore.utils import getToolByName

logger = logging.getLogger("senaite.core")

def _as_list(v):
    if not v:
        return []
    if isinstance(v, string_types):
        return [v]
    return list(v)

class CreateWorksheetModal(Modal):
    """Modal form handler that allows to assign all analyses to a new worksheet
    or batch-create LabProcessRuns for selected samples.
    """
    template = ViewPageTemplateFile("templates/create_worksheet.pt")

    def __init__(self, context, request):
        super(CreateWorksheetModal, self).__init__(context, request)

    def __call__(self):
        if self.request.form.get("submitted", False):
            return self.handle_submit(REQUEST=self.request)
        return self.template()

    def add_status_message(self, message, level="info"):
        """Set a portal status message
        """
        return self.context.plone_utils.addPortalMessage(message, level)

    def get_selected_samples(self):
        """Return selected samples
        """
        return self._resolve_samples_from_uids(self.uids)

    def _resolve_samples_from_uids(self, uids):
        samples = []
        seen = set()
        for uid in (uids or []):
            obj = api.get_object(uid)
            if not obj:
                continue
            pt = getattr(obj, "portal_type", "") or ""
            if pt in ("AnalysisRequest", "Sample"):
                ar = obj
            elif pt in ("Analysis",):
                ar = (getattr(obj, "getRequest", lambda: None)() or getattr(obj, "aq_parent", None))
            else:
                continue

            if not ar:
                continue
            ar_uid = api.get_uid(ar)
            if ar_uid in seen:
                continue
            seen.add(ar_uid)
            samples.append(ar)
        return samples

    def _normalize_service_keywords(self, values):
        """values 里可能是 service UID 或 keyword，统一返回 keyword 列表"""
        kws = []
        for v in (values or []):
            if api.is_uid(v):
                obj = api.get_object(v)
                if obj:
                    kw = getattr(obj, "getKeyword", lambda: None)()
                    if kw:
                        kws.append(kw)
                continue
            # 非 UID 视为 keyword
            kws.append(v)
        return kws

    def _service_uids_from_values(self, values, keywords=None):
        """从 values 中提取 UID，如果 values 是 keyword，则尝试按 keyword 反查 UID"""
        uids = []
        values = _as_list(values)
        for v in values:
            if api.is_uid(v):
                uids.append(v)

        # 如果没有 uid，但有 keywords：尝试 keyword -> uid
        if not uids and keywords:
            kw_to_uid = self._kw_to_uid_map()
            for kw in keywords:
                uid = kw_to_uid.get(kw)
                if uid:
                    uids.append(uid)
        return [u for u in uids if u]

    def _kw_to_uid_map(self):
        """构建 keyword -> uid 映射（active services）"""
        m = {}
        for it in self._all_active_services():
            kw = it.get("keyword")
            uid = it.get("uid")
            if kw and uid:
                m[kw] = uid
        return m

    def get_lab_processes(self):
        """返回所有 LabProcess 供前端下拉选择"""
        pc = api.get_tool("portal_catalog")
        brains = pc(portal_type="LabProcess", sort_on="sortable_title")
        return [{"uid": b.UID, "title": b.Title} for b in brains]

    def get_lab_processes_json(self):
        """返回 JSON 格式的 LabProcess 列表"""
        return json.dumps(self.get_lab_processes())

    def _handle_create_processruns(self, process_uid):
        """
        批量给选中样本创建 LabProcessRun（幂等：重试时自动复用已有 Run）

        事务策略：
        - 每个样本独立提交事务，避免长事务与前端并发冲突
        - ConflictError retry 时走 existing 分支，只更新索引
        - existing 分支的 AR reindex 延迟到循环结束后统一提交
          （避免与新建分支的 commit 产生版本冲突）
        """
        from senaite.core.labprocess.browser.create_processrun import (
            _get_pipeline_stages,
            _create_or_reuse_run,
            _create_first_taskrun,
            _set_analysis_conditions,
        )

        pc = api.get_tool("portal_catalog")
        brains = pc(UID=process_uid)
        if not brains:
            self.add_status_message(_("LabProcess not found"), level="error")
            return self.template()

        process = brains[0].getObject()
        stages = _get_pipeline_stages(process)
        if not stages:
            self.add_status_message(_("Pipeline stages is empty"), level="error")
            return self.template()

        samples = self.get_selected_samples()
        if not samples:
            self.add_status_message(_("No samples selected"), level="error")
            return self.template()

        # 读取批量 conditions
        conditions_map = {}
        conditions_raw = (
                self.request.form.get("conditions_per_sample") or
                self.request.get("conditions_per_sample") or ""
        )

        if conditions_raw:
            try:
                try:
                    parsed = json.loads(conditions_raw)
                except Exception:
                    parsed = conditions_raw
                if isinstance(parsed, dict):
                    conditions_map = parsed
            except Exception as e:
                logger.warning("[CreateWorksheetModal] parse conditions_per_sample failed: %s", e)

        ok = 0
        skipped = 0
        failed = 0

        import transaction as _transaction

        # 延迟 AR reindex 列表：existing 分支处理完的样本
        # 所有样本处理完后统一做一次事务提交，避免与新建分支 commit 产生版本冲突
        deferred_ar_reindex = []

        for sample in samples:
            sample_id = api.get_id(sample)
            sample_uid = api.get_uid(sample)

            sp = None
            try:
                sp = _transaction.savepoint(optimistic=True)
            except Exception:
                pass

            try:
                base_run_id = "processrun-%s" % api.to_utf8(process_uid)[-6:]
                existing_ids = list(sample.objectIds())

                # 幂等检查：找已有 active run
                existing_run = None
                for obj_id in existing_ids:
                    if obj_id == base_run_id or obj_id.startswith(base_run_id + "-"):
                        obj = sample.get(obj_id)
                        if obj and getattr(obj, "portal_type", "") == "LabProcessRun":
                            tpl_uid = getattr(obj, "template_uid", "")
                            if tpl_uid == process_uid:
                                status = getattr(obj, "status", "")
                                if status == "active":
                                    existing_run = obj
                                    break

                if existing_run is not None:
                    # 已有 active run：检查是否需要补写 conditions
                    condition_values = conditions_map.get(sample_uid, [])
                    if condition_values:
                        for tr_obj in existing_run.objectValues():
                            if getattr(tr_obj, "portal_type", "") == "LabTaskRun":
                                try:
                                    _set_analysis_conditions(sample, tr_obj, condition_values)
                                except Exception:
                                    logger.exception(
                                        "[CreateWorksheetModal] update conditions failed "
                                        "sample=%s", sample_id
                                    )
                                break

                    # retry 时：确保 Analysis 索引正确
                    try:
                        from senaite.core.labprocess.analysis_utils import (
                            _reindex_analysis_catalog, _force_process_queue
                        )
                        from bika.lims import api as _api
                        _portal = _api.get_portal()

                        reindexed = 0
                        for _tr in existing_run.objectValues():
                            if getattr(_tr, "portal_type", "") != "LabTaskRun":
                                continue
                            _tr_state = getattr(_tr, "status", "") or ""
                            if _tr_state in ("retracted", "invalid", "cancelled"):
                                continue
                            for _uid in (getattr(_tr, "analysis_uids", []) or []):
                                try:
                                    # 用 catalog 重新获取，避免 ZODB 缓存旧状态
                                    _ac = _api.get_tool("senaite_catalog_analysis")
                                    _brains = _ac.unrestrictedSearchResults(UID=_uid)
                                    if _brains:
                                        _ana = _brains[0].getObject()
                                    else:
                                        _ana = _api.get_object_by_uid(_uid)
                                    if not _ana:
                                        continue
                                    # 强制刷新，确保从数据库读取最新状态
                                    try:
                                        _ana._p_invalidate()
                                    except Exception:
                                        pass
                                    _ana.reindexObject()
                                    _ana.reindexObjectSecurity()
                                    _reindex_analysis_catalog(_ana, _portal)
                                    reindexed += 1
                                except Exception:
                                    logger.exception(
                                        "[CreateWorksheetModal] analysis reindex failed "
                                        "uid=%s sample=%s", _uid, sample_id
                                    )

                        _force_process_queue()

                    except Exception:
                        logger.exception(
                            "[CreateWorksheetModal] existing run reindex failed "
                            "sample=%s", sample_id
                        )

                    # 立即提交 Analysis 条件修改 + 索引
                    # 必须在这里 commit，否则 setConditions 的修改会在后续 abort 时丢失
                    try:
                        from Products.CMFCore.indexing import processQueue
                        processQueue()
                    except Exception:
                        pass

                    try:
                        _transaction.commit()
                    except Exception as _ae:
                        logger.warning(
                            "[CreateWorksheetModal] existing analysis commit failed "
                            "sample=%s err=%s", sample_id, _ae
                        )
                        try:
                            _transaction.abort()
                        except Exception:
                            pass

                    # AR reindex 延迟到循环结束后统一提交
                    # 避免与新建分支的 commit 产生版本冲突导致 ConflictError
                    deferred_ar_reindex.append((sample_id, sample_uid))
                    skipped += 1
                    continue

                # 新建流程
                run = _create_or_reuse_run(sample, process, process_uid, stages)
                tr = _create_first_taskrun(run, sample, stages[0])

                condition_values = conditions_map.get(sample_uid, [])
                if condition_values:
                    _set_analysis_conditions(sample, tr, condition_values)

                # 每个样本独立提交，防止 ConflictError 时队列清空
                try:
                    from Products.CMFCore.indexing import processQueue
                    processQueue()
                except Exception:
                    pass

                try:
                    _transaction.commit()
                    ok += 1
                except Exception as commit_err:
                    logger.warning(
                        "[CreateWorksheetModal] commit failed for sample=%s err=%s",
                        sample_id, commit_err
                    )
                    try:
                        _transaction.abort()
                    except Exception:
                        pass
                    # commit 失败后检查是否已创建成功（另一个 worker 可能已提交）
                    try:
                        sample = api.get_object_by_uid(sample_uid)
                        already_ok = False
                        if sample:
                            _base_run_id = "processrun-%s" % api.to_utf8(process_uid)[-6:]
                            for _obj_id in sample.objectIds():
                                if _obj_id == _base_run_id or _obj_id.startswith(_base_run_id + "-"):
                                    _obj = sample.get(_obj_id)
                                    if (_obj and
                                            getattr(_obj, "portal_type", "") == "LabProcessRun" and
                                            getattr(_obj, "template_uid", "") == process_uid and
                                            getattr(_obj, "status", "") == "active"):
                                        already_ok = True
                                        break
                        if already_ok:
                            skipped += 1
                        else:
                            failed += 1
                            logger.warning("[CreateWorksheetModal] commit failed, no run found, failed sample=%s", sample_id)
                    except Exception:
                        failed += 1
                        logger.exception(
                            "[CreateWorksheetModal] post-commit check failed sample=%s",
                            sample_id
                        )

            except Exception:
                if sp is not None:
                    try:
                        sp.rollback()
                    except Exception:
                        try:
                            _transaction.abort()
                        except Exception:
                            pass
                else:
                    try:
                        _transaction.abort()
                    except Exception:
                        pass
                failed += 1
                logger.exception(
                    "[CreateWorksheetModal] failed to create processrun for sample=%s",
                    sample_id
                )

        # -------------------------------------------------------
        # 延迟 AR reindex：所有样本处理完后统一提交
        # existing 分支的样本 AR reindex 在这里统一做
        # 此时所有新建分支的 commit 已完成，不会再有版本冲突
        # -------------------------------------------------------
        if deferred_ar_reindex:
            reindex_ok = 0
            reindex_fail = 0
            for _sample_id, _sample_uid in deferred_ar_reindex:
                try:
                    _sample_obj = api.get_object_by_uid(_sample_uid)
                    if _sample_obj:
                        _sample_obj._p_invalidate()
                        _sample_obj.reindexObject(idxs=['getAnalysesNum', 'getProgress'])
                        reindex_ok += 1
                except Exception:
                    reindex_fail += 1
                    logger.exception(
                        "[CreateWorksheetModal] deferred ar reindex failed sample=%s",
                        _sample_id
                    )

            try:
                from Products.CMFCore.indexing import processQueue
                processQueue()
            except Exception:
                pass

            try:
                _transaction.commit()
            except Exception as _de:
                logger.warning(
                    "[CreateWorksheetModal] deferred ar reindex commit failed err=%s", _de
                )
                try:
                    _transaction.abort()
                except Exception:
                    pass

        if ok:
            self.add_status_message(
                _("Successfully created process run for %s sample(s)" % ok),
                level="info"
            )
        if skipped:
            self.add_status_message(
                _("Skipped %s sample(s) with existing active process run" % skipped),
                level="info"
            )
        if failed:
            self.add_status_message(
                _("Failed to create process run for %s sample(s)" % failed),
                level="error"
            )

        portal_url = api.get_url(api.get_portal())
        return self.request.response.redirect(portal_url + "/samples")


    def handle_submit(self, REQUEST=None):
        """区分创建 ProcessRun 还是创建 Worksheet"""

        # 优先判断是否选了实验流程（与工作表互斥）
        process_uid = (self.request.form.get("process_uid") or "").strip()
        if process_uid:
            return self._handle_create_processruns(process_uid)

        # 否则走原有 Worksheet 逻辑
        profiles = _as_list(self.request.form.get("profiles"))
        profiles = list(filter(api.is_uid, profiles or []))
        profile_uid = profiles[0] if profiles else None

        raw_services = self.request.form.get("services") or self.request.form.get("services:list")
        services_vals = _as_list(raw_services)
        services_vals = [s for s in services_vals if s]

        if not services_vals and not profile_uid:
            self.add_status_message(_("Please select an analysis service or profile"), level="error")
            return self.template()

        service_keywords = self._normalize_service_keywords(services_vals)
        service_keywords = list(filter(None, service_keywords))
        service_uids = self._service_uids_from_values(services_vals, keywords=service_keywords)

        worksheet = self.create_worksheet_for(
            self.get_selected_samples(),
            profile_uid=profile_uid,
            service_uids=service_uids,
            service_keywords=service_keywords,
        )

        if not worksheet:
            return self.template()

        self.add_status_message(_("Created worksheet %s" % api.get_id(worksheet)), level="info")
        return api.get_url(worksheet)

    def create_worksheet_for(self, samples, profile_uid=None, service_uids=None, service_keywords=None):
        """Create a new worksheet"""
        service_uids = list(service_uids or [])
        service_keywords = set(service_keywords or [])

        samples = [s if hasattr(s, "getAnalyses") else api.get_object(s) for s in (samples or [])]
        samples = [s for s in samples if s]

        if profile_uid:
            profile = api.get_object(profile_uid)
            profile_uid = api.get_uid(profile)
            for sample in samples:
                self._set_sample_profile(sample, profile_uid)

        if service_uids:
            for sample in samples:
                self._set_sample_services(sample, service_uids)

        analyses = []
        stats = {"total": 0, "not_unassigned": 0, "not_allowed": 0, "not_selected": 0, "included": 0}

        for sample in samples:
            sid = api.get_id(sample)
            try:
                all_analyses = sample.getAnalyses(full_objects=True) or []
            except Exception:
                all_analyses = []

            for analysis in all_analyses:
                stats["total"] += 1
                try:
                    state = api.get_workflow_status_of(analysis)
                except Exception:
                    state = None

                kw = None
                try:
                    kw = analysis.getKeyword()
                except Exception:
                    pass

                if state != "unassigned":
                    stats["not_unassigned"] += 1
                    continue

                if service_keywords and kw and kw not in service_keywords:
                    stats["not_selected"] += 1
                    continue

                analyses.append(analysis)
                stats["included"] += 1

        if not analyses:
            logger.error(
                "[CreateWorksheetModal] NO analyses after filters. stats=%r service_keywords=%r service_uids=%r",
                stats, sorted(list(service_keywords))[:50], service_uids)
            self.add_status_message(_("No unassigned analyses found for selected services"), level="error")
            return None

        ws = api.create(self.worksheet_folder, "Worksheet")
        ws.setResultsLayout(self.worksheet_layout)
        ws.addAnalyses(analyses)
        return ws

    @property
    def worksheet_folder(self):
        """Return the worksheet root folder
        """
        portal = api.get_portal()
        return portal.restrictedTraverse("worksheets")

    @property
    def worksheet_layout(self):
        """Return the configured workheet layout
        """
        setup = api.get_setup()
        return setup.getWorksheetLayout()

    def get_analysis_categories(self):
        categories = []
        all_keywords = set()
        keyword_to_category = {}

        for sample in self.get_selected_samples():
            for analysis in sample.getAnalyses(full_objects=True):
                if api.get_workflow_status_of(analysis) != "unassigned":
                    continue

                keyword = analysis.getKeyword()
                category = analysis.getCategory()
                if not keyword or not category:
                    continue

                all_keywords.add(keyword)
                keyword_to_category[keyword] = category

        allowed = get_allowed_keywords(self.context)
        if allowed == "__ALL__":
            allowed_keywords = all_keywords
        else:
            allowed_set = set(allowed or [])
            allowed_keywords = [kw for kw in all_keywords if kw in allowed_set]

        for kw in allowed_keywords:
            cat = keyword_to_category.get(kw)
            if cat and cat not in categories:
                categories.append(cat)

        categories = list(
            map(self.get_category_info, sorted(categories, key=lambda c: c.getSortKey()))
        )
        return categories

    def get_category_info(self, category):
        """Extract category information for template
        """
        return {
            "title": api.get_title(category),
            "uid": api.get_uid(category),
            "obj": category,
        }

    def get_profiles(self):
        portal = api.get_portal()

        cat = getToolByName(portal, "senaite_catalog_setup")
        brains = cat.searchResults(
            portal_type="AnalysisProfile",
            review_state="active",
            sort_on="sortable_title",
        )

        profiles = []
        for b in brains:
            obj = b.getObject() if hasattr(b, "getObject") else api.get_object(b)
            if not obj:
                continue
            profiles.append({
                "title": api.get_title(obj),
                "uid": api.get_uid(obj),
                "obj": obj,
            })

        return profiles

    def _set_sample_profile(self, sample, profile_uid):
        """把 profile 写到样本 Profiles 字段，并触发根据套餐生成分析项目"""
        try:
            if hasattr(sample, "setProfiles"):
                sample.setProfiles([profile_uid])
            else:
                field = sample.getField("Profiles")
                if field:
                    field.set(sample, [profile_uid])

            for m in ("updateAnalyses", "update_services", "reindexObject", "_reindexAnalyses"):
                fn = getattr(sample, m, None)
                if callable(fn):
                    try:
                        fn()
                    except Exception:
                        logger.exception("[CreateWorksheetModal] Calling %s() failed for sample %s", m,
                                         api.get_id(sample))

        except Exception:
            logger.exception("[CreateWorksheetModal] Failed to set profile %s for sample %s", profile_uid,
                             api.get_id(sample))
            raise

    def _allowed_keyword_set(self):
        allowed = get_allowed_keywords(self.context)
        if allowed == "__ALL__":
            return None  # None 表示不限制
        return set(allowed or [])

    def _all_active_services(self):
        portal = api.get_portal()
        cat = getToolByName(portal, "senaite_catalog_setup")
        brains = cat.searchResults(
            portal_type="AnalysisService",
            review_state="active",
            sort_on="sortable_title",
        )
        res = []
        for b in brains:
            obj = b.getObject() if hasattr(b, "getObject") else api.get_object(b)
            if not obj:
                continue
            kw = getattr(obj, "getKeyword", lambda: None)()
            if not kw:
                continue
            res.append({"title": api.get_title(obj), "keyword": kw, "uid": api.get_uid(obj)})
        return res

    def get_all_service_options(self):
        """不选套餐时：列出当前用户能看到的服务"""
        allowed_set = self._allowed_keyword_set()
        items = self._all_active_services()
        if allowed_set is None:
            return items
        return [x for x in items if x["keyword"] in allowed_set]

    def get_all_service_options_json(self):
        return json.dumps(self.get_all_service_options())

    def get_profile_to_services_map(self):
        """返回 {profile_uid: [{title, keyword, uid}, ...]}（已叠加 allowed 过滤）"""
        allowed_set = self._allowed_keyword_set()
        all_services = {x["keyword"]: x for x in self._all_active_services()}
        out = {}
        for p in self.get_profiles():
            uid = p["uid"]
            profile = p["obj"]
            kws = self._profile_keywords_guess(profile)
            if allowed_set is not None:
                kws = set([k for k in kws if k in allowed_set])
            out[uid] = [all_services[k] for k in kws if k in all_services]
        return out

    def get_profile_to_services_json(self):
        return json.dumps(self.get_profile_to_services_map())

    def _profile_keywords_guess(self, profile):
        candidates = []
        for name in ("getServiceUIDs", "getServices", "getAnalysisServices", "getService", "Services", "Service"):
            v = getattr(profile, name, None)
            if callable(v):
                try:
                    candidates = v()
                    break
                except Exception:
                    pass
            else:
                try:
                    field = profile.getField(name) if hasattr(profile, "getField") else None
                    if field:
                        candidates = field.get(profile)
                        break
                except Exception:
                    pass

        kws = set()
        for x in (candidates or []):
            try:
                if hasattr(x, "getObject"):
                    x = x.getObject()
            except Exception:
                pass
            obj = api.get_object(x) if api.is_uid(x) else x
            if not obj:
                continue
            kw = getattr(obj, "getKeyword", lambda: None)()
            if kw:
                kws.add(kw)
        return kws

    def _set_sample_services(self, sample, service_uids):
        """把选中的 AnalysisService 写入 AR，并触发生成/同步 analyses"""
        service_uids = list(service_uids or [])
        if not service_uids:
            return

        sid = api.get_id(sample)

        existing_services = []
        try:
            for an in (sample.getAnalyses(full_objects=True) or []):
                try:
                    svc = an.getAnalysisService()
                    if svc:
                        existing_services.append(svc)
                except Exception:
                    pass
        except Exception:
            logger.exception("[CreateWorksheetModal] cannot read existing analyses/services for %s", sid)

        selected_services = []
        for uid in service_uids:
            try:
                obj = api.get_object(uid)
                if obj:
                    selected_services.append(obj)
            except Exception:
                logger.exception("[CreateWorksheetModal] cannot resolve service uid=%s", uid)

        union = {}
        for s in (existing_services + selected_services):
            try:
                union[api.get_uid(s)] = s
            except Exception:
                pass

        services_to_set = [union[k] for k in union.keys()]

        try:
            if hasattr(sample, "setAnalyses"):
                sample.setAnalyses(services_to_set)
            else:
                logger.warning("[CreateWorksheetModal] target %s has no setAnalyses()", sid)
        except Exception:
            logger.exception("[CreateWorksheetModal] setAnalyses failed for %s", sid)

        for m in ("_reindexAnalyses", "reindexObject"):
            fn = getattr(sample, m, None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    logger.exception("[CreateWorksheetModal] %s failed for %s", m, sid)