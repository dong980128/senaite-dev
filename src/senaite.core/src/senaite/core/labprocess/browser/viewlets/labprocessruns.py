# -*- coding: utf-8 -*-
"""
viewlets/labprocessruns.py
在 Sample 页面嵌入展示 LabProcessRun 列表。

职责：
  1. get_processes()     — 提供流程选择下拉框数据
  2. runs()              — 返回当前 Sample 下所有 Run 及其 TaskRun 列表
  3. get_taskruns(run)   — 返回单个 Run 下所有 TaskRun 的展示数据
  4. render_full_task_analyses() — 在每个 TaskRun 下嵌入 Analysis 列表
"""

import re
import logging

from bika.lims import api
from plone.app.layout.viewlets.common import ViewletBase
from Products.CMFCore.utils import getToolByName
from senaite.core.labprocess.utils_common import (
    as_list,
    get_pipeline,
    get_uid,
    norm_step,
    to_unicode,
    wf_state,
)

from senaite.core.labprocess.utils_auth import get_username
try:
    from urllib import quote as _url_quote
except Exception:
    _url_quote = None
from zope.i18n import translate

logger = logging.getLogger(__name__)


class LabProcessRunsViewlet(ViewletBase):

    analysis_listing_view = "labprocess_task_analyses"

    def get_processes(self):
        """返回所有 LabProcess 供前端下拉选择。"""
        pc = api.get_tool("portal_catalog")
        brains = pc(portal_type="LabProcess", sort_on="sortable_title")
        return [{"uid": b.UID, "title": b.Title} for b in brains]

    def get_current_process(self):
        """返回当前 Sample 已关联的 LabProcess（取第一个有效 Run 对应的流程）。
        若无有效 Run 则返回 None，用于初始化选择器标签。
        """
        try:
            ar = self.context
            pc = api.get_tool("portal_catalog")
            ar_path = "/".join(ar.getPhysicalPath())
            brains = pc(
                portal_type="LabProcessRun",
                path={"query": ar_path, "depth": 1},
                sort_on="created",
                sort_order="reverse",
            )
            for b in brains:
                run = b.getObject()
                # 尝试从 run 上读关联的 LabProcess
                lp = None
                for getter in ("getLabProcess", "getProcess", "lab_process"):
                    val = getattr(run, getter, None)
                    if callable(val):
                        try:
                            lp = val()
                        except Exception:
                            pass
                    elif val is not None:
                        lp = val
                    if lp:
                        break
                if lp:
                    return {
                        "uid": to_unicode(api.get_uid(lp) or ""),
                        "title": to_unicode(lp.Title() if callable(lp.Title) else lp.Title),
                    }
                # fallback：从 run title 提取（格式 "{流程名} - Run"）
                title = to_unicode(b.Title or "")
                if " - Run" in title:
                    process_title = title.rsplit(" - Run", 1)[0].strip()
                    # 在 LabProcess 里找匹配 title
                    lp_brains = pc(portal_type="LabProcess", Title=process_title)
                    if lp_brains:
                        return {
                            "uid": to_unicode(lp_brains[0].UID),
                            "title": to_unicode(lp_brains[0].Title),
                        }
                    return {"uid": "", "title": process_title}
        except Exception:
            logger.exception("[labprocessruns] get_current_process failed")
        return None

    def runs(self):
        """
        返回当前 Sample 下所有 LabProcessRun 的展示数据列表。
        支持 lp_runs_filter 参数：valid（默认）/ invalid / all
        """
        ar = self.context
        catalog = getToolByName(ar, "portal_catalog")
        ar_path = "/".join(ar.getPhysicalPath())
        flt = (self.request.get("lp_runs_filter") or "valid").strip().lower()

        brains = catalog(
            portal_type="LabProcessRun",
            path={"query": ar_path, "depth": 1},
            sort_on="created",
            sort_order="reverse",
        )

        items = []
        for b in brains:
            run = b.getObject()
            state = to_unicode(getattr(run, "status", "") or wf_state(run) or "").strip().lower()
            is_invalid = state in ("cancelled", "canceled", "inactive", "invalid", "retracted", "rejected")

            if flt == "valid" and is_invalid:
                continue
            if flt == "invalid" and not is_invalid:
                continue

            items.append({
                "obj": run,
                "uid": b.UID,
                "title": b.Title,
                "url": b.getURL(),
                "state": state,
                "created": getattr(b, "created", None),
                "taskruns": self.get_taskruns(run),
                "can_cancel": not is_invalid,
            })

        return items

    def get_taskruns(self, run):
        """
        返回单个 Run 下所有 TaskRun 的展示数据列表（按 step 升序）。
        """
        pc = api.get_tool("portal_catalog")
        brains = pc(
            portal_type="LabTaskRun",
            path={"query": "/".join(run.getPhysicalPath()), "depth": 1},
            sort_on="created",
            sort_order="ascending",
        )

        username = to_unicode(get_username())
        stage_map = self._stage_map_by_step(run)
        wf_graph = self._get_workflow_graph("senaite_labtaskrun_workflow")

        # Manager/LabManager 看全部，其他人只看分配给自己的 TaskRun
        from bika.lims import api as _api
        import logging as _logging
        _logger = _logging.getLogger(__name__)
        current_user = _api.get_current_user()
        current_roles = set(current_user.getRoles() if current_user else [])
        is_manager = bool({"Manager"} & current_roles)  # 只有 Manager 看全部，LabManager 只看自己的
        _logger.info("[labprocessruns] get_taskruns username=%r roles=%r is_manager=%r",
                     username, sorted(current_roles), is_manager)

        items = []
        for b in brains:
            tr = b.getObject()
            item = self._build_taskrun_item(tr, b, username, stage_map, wf_graph)
            _logger.info("[labprocessruns] taskrun=%r is_mine=%r",
                        item.get("title"), item.get("is_mine"))
            if not is_manager and not item.get("is_mine", False):
                continue
            items.append(item)

        items.sort(key=lambda x: _safe_int(x.get("step") or 0))

        # 每个 step 只保留最新的非终态 TaskRun
        # retracted/invalid 的历史记录完全隐藏
        HIDE_STATES = {"retracted", "invalid", "cancelled"}
        step_seen = set()
        filtered = []
        # 倒序遍历，优先取每个 step 最新的非终态
        for item in reversed(items):
            step = str(item.get("step") or "")
            state = (item.get("status") or item.get("review_state") or "").lower()
            if state in HIDE_STATES:
                continue
            if step in step_seen:
                continue
            step_seen.add(step)
            filtered.append(item)
        # 恢复正序
        filtered.reverse()

        return filtered

    def get_analyses_by_services(self, services):
        """
        在当前 AR 下，按 service keyword 找到对应的 Analysis 对象列表。
        返回 [{"uid": ..., "object": ..., "service_keyword": ...}]
        """
        tokens = [to_unicode(x).strip() for x in as_list(services) if x]
        tokens = [t for t in tokens if t]
        if not tokens:
            return []

        want = set(t.lower() for t in tokens)
        out = []

        for a in self._iter_ar_analyses():
            try:
                svc = (getattr(a, "getAnalysisService", None) or (lambda: None))()
                if not svc:
                    svc = (getattr(a, "getService", None) or (lambda: None))()
                if not svc:
                    continue
                kw = to_unicode(getattr(svc, "getKeyword", lambda: "")() or "").strip()
                if kw and kw.lower() in want:
                    out.append({
                        "uid": to_unicode(api.get_uid(a) or "").strip(),
                        "object": a,
                        "service_keyword": kw,
                    })
            except Exception as e:
                logger.warning("[labprocessruns] get_analyses_by_services failed: %s", e)

        return out

    def render_full_task_analyses(self, t, run_uid=None):
        """
        为单个 TaskRun 渲染嵌入式 Analysis 列表。
        注意：update() 重复调用是为了兼容部分 SENAITE 版本的 ListingView 初始化问题。
        """
        request = self.request
        task_uid = to_unicode((t or {}).get("uid") or u"").strip()
        mode = to_unicode((t or {}).get("mode") or u"").strip().lower()

        # 1. 优先用 TaskRun 上记录的 analysis_uids
        uids = [to_unicode(x).strip()
                for x in as_list((t or {}).get("analysis_uids") or []) if x]

        # 2. analysis 模式且没有 uids：按 services 从 AR 上查
        if not uids and mode == "analysis":
            services = (t or {}).get("services") or []
            matches = self.get_analyses_by_services(services) or []
            uids = [to_unicode(x.get("uid") or u"").strip() for x in matches if x.get("uid")]

        if not uids:
            return u""

        listing_uid = u"lp_task_analyses_%s" % (task_uid or u"x")
        analysis_uids_raw = u",".join([to_unicode(x) for x in uids if x])

        # 准备注入 request 的参数
        inject = {
            "form_id": listing_uid,
            "listing_form_id": listing_uid,
            "analysis_uids": analysis_uids_raw,
            "taskrun_uid": task_uid,
            "processrun_uid": to_unicode(run_uid or u""),
            listing_uid + "_analysis_uids": analysis_uids_raw,
            listing_uid + "_taskrun_uid": task_uid,
            listing_uid + "_processrun_uid": to_unicode(run_uid or u""),
        }

        # 备份并注入 request
        old_form = dict(getattr(request, "form", {}) or {})
        old_other = dict(getattr(request, "other", {}) or {})
        html = u""

        try:
            request.form.clear()
            request.form.update(inject)
            try:
                request.other.clear()
                request.other.update(inject)
            except Exception:
                pass

            view = self.context.restrictedTraverse("@@%s" % self.analysis_listing_view)

            # update() 调用（兼容部分版本需要调两次）
            for _ in range(2):
                try:
                    upd = getattr(view, "update", None)
                    if callable(upd):
                        upd()
                except Exception as e:
                    logger.warning("[labprocessruns] view.update() failed: %s", e)

            # 渲染 listing HTML
            try:
                ct = getattr(view, "contents_table", None)
                if callable(ct):
                    html = ct() or u""
            except Exception as e:
                logger.warning("[labprocessruns] contents_table() failed: %s", e)

            if not html:
                try:
                    tpl = getattr(view, "contents_table_template", None)
                    tpl = tpl() if callable(tpl) else tpl
                    html = tpl() if callable(tpl) else (tpl or u"")
                except Exception as e:
                    logger.warning("[labprocessruns] contents_table_template failed: %s", e)

        except Exception as e:
            logger.error("[labprocessruns] render_full_task_analyses failed fid=%r err=%s",
                         listing_uid, e, exc_info=True)
        finally:
            # 恢复 request
            request.form.clear()
            request.form.update(old_form)
            try:
                request.other.clear()
                request.other.update(old_other)
            except Exception:
                pass

        html = html or u""

        # patch：替换 listing form id、data-url
        try:
            html = self._patch_listing_html_ids(html, u"analyses_form", listing_uid)
            html = html.replace(u"modal_analyses_form", u"modal_%s" % listing_uid)
            html = self._patch_listing_data_url(
                html, listing_uid, analysis_uids_raw, task_uid, run_uid
            )
        except Exception as e:
            logger.warning("[labprocessruns] patch html failed fid=%r err=%s",
                           listing_uid, e, exc_info=True)

        # 注入隐藏字段
        hidden = (
            u'<span data-lp-injected="1"></span>'
            u'<input type="hidden" name="form_id" value="{fid}" />'
            u'<input type="hidden" name="listing_form_id" value="{fid}" />'
            u'<input type="hidden" name="analysis_uids" value="{auids}" />'
            u'<input type="hidden" name="{fid}_analysis_uids" value="{auids}" />'
            u'<input type="hidden" name="taskrun_uid" value="{tid}" />'
            u'<input type="hidden" name="processrun_uid" value="{pid}" />'
            u'<input type="hidden" name="{fid}_taskrun_uid" value="{tid}" />'
            u'<input type="hidden" name="{fid}_processrun_uid" value="{pid}" />'
        ).format(
            fid=to_unicode(listing_uid).replace(u'"', u"&quot;"),
            auids=to_unicode(analysis_uids_raw).replace(u'"', u"&quot;"),
            tid=to_unicode(task_uid).replace(u'"', u"&quot;"),
            pid=to_unicode(run_uid or u"").replace(u'"', u"&quot;"),
        )

        html = self._inject_after_form_tag(html, listing_uid, hidden)

        return (
            u'<div class="lp-embedded-listing" '
            u'data-form-id="{fid}" '
            u'data-taskrun-uid="{tid}" '
            u'data-processrun-uid="{pid}" '
            u'data-analysis-uids="{auids}">'
            u'{listing}'
            u'</div>'
        ).format(
            fid=to_unicode(listing_uid),
            tid=to_unicode(task_uid),
            pid=to_unicode(run_uid or u""),
            auids=to_unicode(analysis_uids_raw),
            listing=html,
        )

    # -----------------------------------------------------------------------
    # 内部辅助：TaskRun 数据构建
    # -----------------------------------------------------------------------

    def _build_taskrun_item(self, tr, brain, username, stage_map, wf_graph):
        """把单个 TaskRun 对象转成模板用的 dict。"""

        # step
        step = ""
        try:
            step = tr.getStep()
        except Exception:
            step = getattr(tr, "step", "") or ""
        step = str(step) if step is not None else ""

        # title
        try:
            title = tr.Title()
        except Exception:
            title = getattr(tr, "title", "") or getattr(tr, "task_name", "") or ""

        # users
        try:
            users = as_list(tr.getAssignedUsers())
        except Exception:
            users = as_list(getattr(tr, "assigned_users", ""))

        is_mine = bool(username and username in users)

        # status：优先 review_state，再 status 字段
        review_state = to_unicode(wf_state(tr) or "").strip().lower()
        status = review_state
        if not status:
            status = to_unicode(getattr(tr, "status", "") or "").strip().lower()
            if not status:
                try:
                    status = to_unicode(tr.getStatus() or "").strip().lower()
                except Exception:
                    status = ""

        # 工作流 transitions
        transitions = self._wf_transitions(tr)
        all_ds = (wf_graph or {}).get(review_state, [])
        allowed = set(to_unicode(x).strip().lower() for x in transitions if x)

        downstream = [
            {
                "id": to_unicode(x.get("id") or u"").strip().lower(),
                "title": to_unicode(x.get("title") or x.get("id") or u""),
                "new_state": to_unicode(x.get("new_state") or u""),
                "enabled": (to_unicode(x.get("id") or u"").strip().lower() in allowed),
            }
            for x in (all_ds or [])
        ]

        can_complete = ("complete" in transitions)

        # mode / handler / services：stage 优先，TaskRun 兜底
        step_key = norm_step(step)
        stage = stage_map.get(step_key, {}) if step_key else {}

        mode = (to_unicode(stage.get("mode") or "").strip().lower()
                or to_unicode(getattr(tr, "mode", "") or "").strip().lower()
                or "analysis")
        handler = (to_unicode(stage.get("handler") or "").strip()
                   or to_unicode(getattr(tr, "handler", "") or "").strip())
        services = stage.get("services") or getattr(tr, "services", None)
        services_list = as_list(services)

        # analysis_uids
        try:
            analysis_uids = as_list(getattr(tr, "analysis_uids", None) or [])
        except Exception:
            analysis_uids = []

        # analysis 模式但没有 uids 且已完成 -> 状态修正为 running（数据缺失）
        if mode == "analysis" and not analysis_uids:
            if status in ("done", "completed", "complete", "finished"):
                status = "running"

        return {
            "obj": tr,
            "uid": getattr(brain, "UID", ""),
            "url": brain.getURL(),
            "step": step,
            "title": title,
            "users": users,
            "users_text": self._users_fullnames(users),
            "is_mine": is_mine,
            "status": status,
            "review_state": review_state,
            "mode": mode,
            "handler": handler,
            "services": services_list,
            "services_text": self._services_titles(services_list),
            "analysis_uids": analysis_uids,
            "transitions": transitions,
            "can_complete": can_complete,
            "downstream": downstream,
            "conditional_services": list(getattr(tr, "conditional_services", []) or []),
        }

    # -----------------------------------------------------------------------
    # 内部辅助：Pipeline
    # -----------------------------------------------------------------------

    def _services_titles(self, keywords):
        """把 service keyword 列表转成 Title 列表
        从 senaite_catalog_analysis 按 getKeyword 查 Analysis，取其 Title。
        """
        if not keywords:
            return u""
        try:
            ac = api.get_tool("senaite_catalog_analysis")
            titles = []
            for kw in keywords:
                brains = ac(portal_type="Analysis", getKeyword=kw)
                if brains:
                    title = to_unicode(brains[0].Title or kw)
                else:
                    title = to_unicode(kw)
                titles.append(title)
            result = u", ".join(titles)
            return result
        except Exception:
            logger.exception("[labprocessruns] _services_titles failed keywords=%r", keywords)
            return u", ".join([to_unicode(k) for k in keywords])

    def _users_fullnames(self, usernames):
        """把 username 列表转成 fullname 列表"""
        if not usernames:
            return u""
        try:
            mt = api.get_tool("portal_membership")
            names = []
            for uname in usernames:
                member = mt.getMemberById(uname)
                if member:
                    fullname = to_unicode(
                        member.getProperty("fullname", "") or uname
                    ).strip()
                    names.append(fullname or to_unicode(uname))
                else:
                    names.append(to_unicode(uname))
            return u", ".join(names)
        except Exception:
            return u", ".join([to_unicode(u) for u in usernames])

    def _stage_map_by_step(self, run):
        """step(str) -> stage dict 的快速查找表。"""
        m = {}
        for st in get_pipeline(run):
            if not isinstance(st, dict):
                continue
            step = st.get("step") or st.get("Step")
            if step is None:
                continue
            k = norm_step(step)
            if k:
                m[k] = st
        return m

    # -----------------------------------------------------------------------
    # 内部辅助：AR Analysis 遍历
    # -----------------------------------------------------------------------

    def _iter_ar_analyses(self):
        """遍历当前 AR 下所有 Analysis 对象。"""
        ar = self.context

        fn = getattr(ar, "getAnalyses", None)
        if callable(fn):
            try:
                res = fn(full_objects=True)
                return res or []
            except TypeError:
                try:
                    res = fn()
                    if res and hasattr(res[0], "getObject"):
                        res = [b.getObject() for b in res]
                    return res or []
                except Exception:
                    pass
            except Exception:
                pass

        # catalog fallback
        try:
            ar_path = "/".join(ar.getPhysicalPath())
            brains = api.search(
                {"portal_type": "Analysis", "path": {"query": ar_path, "depth": 5}},
                catalog="senaite_catalog_analysis",
            ) or []
            return [b.getObject() for b in brains]
        except Exception as e:
            logger.warning("[labprocessruns] _iter_ar_analyses fallback failed: %s", e)
            return []

    # -----------------------------------------------------------------------
    # 内部辅助：工作流
    # -----------------------------------------------------------------------

    def _wf_transitions(self, obj):
        """返回当前对象允许的 transition id 列表。"""
        wf = api.get_tool("portal_workflow")
        try:
            ts = wf.getTransitionsFor(obj) or []
            return [to_unicode(t.get("id", "")).strip().lower() for t in ts if t.get("id")]
        except Exception as e:
            # fallback：手动查 workflow graph + guard
            return self._wf_transitions_fallback(obj, wf, e)

    def _wf_transitions_fallback(self, obj, wf, original_error):
        """getTransitionsFor 失败时的兜底：从 workflow graph 手动算允许的 transitions。"""
        try:
            chain = wf.getChainFor(obj) or ()
            wf_id = chain[0] if chain else ""
        except Exception:
            wf_id = ""

        if wf_id != "senaite_labtaskrun_workflow":
            return []

        try:
            from bika.lims.workflow.labtaskrun import guards as G
        except Exception:
            return []

        st = to_unicode(wf_state(obj) or "").strip().lower()
        graph = self._get_workflow_graph("senaite_labtaskrun_workflow") or {}
        candidates = [to_unicode(x.get("id") or "").strip().lower()
                      for x in (graph.get(st) or [])]

        check = {
            "complete": getattr(G, "guard_complete", None),
            "retract": getattr(G, "guard_retract", None),
            "invalidate": getattr(G, "guard_invalidate", None),
            "reinstate": getattr(G, "guard_reinstate", None),
        }

        allowed = [tid for tid in candidates
                   if callable(check.get(tid)) and check[tid](obj)]

        logger.warning("[labprocessruns] _wf_transitions fallback state=%s allowed=%s err=%r",
                       st, allowed, original_error)
        return allowed

    def _get_workflow_graph(self, workflow_id="senaite_labtaskrun_workflow"):
        """
        返回 workflow 的状态转换图：{ state_id: [{"id", "title", "new_state"}, ...] }
        """
        wf_tool = api.get_tool("portal_workflow")
        wf = wf_tool.getWorkflowById(workflow_id)
        if not wf:
            return {}

        trans_map = {}
        for tid, t in (getattr(wf, "transitions", {}) or {}).items():
            raw_title = to_unicode(getattr(t, "title", tid) or tid).strip()

            # 用 XML 里声明的 i18n:domain="senaite.core" 做翻译
            translated_title = translate(
                raw_title,
                domain="senaite.core",
                context=self.request,
                default=raw_title,
            )

            trans_map[tid] = {
                "id": to_unicode(tid).strip(),
                # "title": to_unicode(getattr(t, "title", tid) or tid).strip(),
                "title":to_unicode(translated_title).strip(),
                "new_state": to_unicode(getattr(t, "new_state_id", "") or "").strip(),
            }

        state_map = {}
        for sid, s in (getattr(wf, "states", {}) or {}).items():
            exit_ids = list(getattr(s, "transitions", []) or [])
            state_map[sid] = [
                trans_map.get(tid, {"id": tid, "title": tid, "new_state": ""})
                for tid in exit_ids
            ]

        return state_map

    # -----------------------------------------------------------------------
    # 内部辅助：HTML patch
    # -----------------------------------------------------------------------

    def _inject_after_form_tag(self, html, listing_uid, hidden):
        """把 hidden inputs 注入到目标 <form> 标签之后。"""
        if not html:
            return hidden + html

        try:
            # 优先精确匹配目标 form id
            for pat in (
                r'(<form\b[^>]*\bid="%s"[^>]*>)' % re.escape(listing_uid),
                r"(<form\b[^>]*\bid='%s'[^>]*>)" % re.escape(listing_uid),
                r"(<form\b[^>]*>)",
            ):
                m = re.search(pat, html, flags=re.I)
                if m:
                    pos = m.end(1)
                    return html[:pos] + u"\n" + hidden + u"\n" + html[pos:]
        except Exception:
            logger.warning("[labprocessruns] _inject_after_form_tag failed", exc_info=True)

        return hidden + html

    def _patch_listing_html_ids(self, html, old_id, new_id):
        """把 listing HTML 里所有 old_id 替换成 new_id。"""
        if not html or old_id == new_id:
            return html
        replacements = [
            (u'id="%s"' % old_id, u'id="%s"' % new_id),
            (u"id='%s'" % old_id, u"id='%s'" % new_id),
            (u'data-form-id="%s"' % old_id, u'data-form-id="%s"' % new_id),
            (u"data-form-id='%s'" % old_id, u"data-form-id='%s'" % new_id),
            (u'name="%s"' % old_id, u'name="%s"' % new_id),
            (u"name='%s'" % old_id, u"name='%s'" % new_id),
            (old_id + u"_", new_id + u"_"),
            (u"#" + old_id, u"#" + new_id),
            (u'"form_id":"%s"' % old_id, u'"form_id":"%s"' % new_id),
        ]
        for old, new in replacements:
            html = html.replace(old, new)
        return html

    def _patch_listing_data_url(self, html, listing_uid, analysis_uids_raw,
                                task_uid=u"", run_uid=u""):
        """给 listing-container 的 data-url 追加必要的查询参数。"""
        html = to_unicode(html or u"")
        fid = to_unicode(listing_uid or u"").strip()
        if not html or not fid:
            return html

        auids = to_unicode(analysis_uids_raw or u"").strip()
        task_uid = to_unicode(task_uid or u"").strip()
        run_uid = to_unicode(run_uid or u"").strip()

        params = [u"form_id=%s" % self._uquote(fid)]
        if auids:
            params.append(u"%s_analysis_uids=%s" % (self._uquote(fid), self._uquote(auids)))
            params.append(u"analysis_uids=%s" % self._uquote(auids))
        if task_uid:
            params.append(u"%s_taskrun_uid=%s" % (self._uquote(fid), self._uquote(task_uid)))
        if run_uid:
            params.append(u"%s_processrun_uid=%s" % (self._uquote(fid), self._uquote(run_uid)))

        add_qs = u"&amp;".join(params)

        def _patch_one(m):
            prefix, url, suffix = m.group(1), to_unicode(m.group(2) or u""), m.group(3)
            if u"ajax_folderitems" not in url:
                return m.group(0)
            joiner = u"&amp;" if u"?" in url else u"?"
            return prefix + url + joiner + add_qs + suffix

        try:
            html = re.sub(r'(data-url=")([^"]*)(\")', _patch_one, html)
            html = re.sub(r"(data-url=')([^']*)(')", _patch_one, html)

            def _patch_js(m):
                url = m.group(2)
                if u"ajax_folderitems" not in url:
                    return m.group(0)
                joiner = u"&" if u"?" in url else u"?"
                return m.group(1) + url + joiner + add_qs.replace(u"&amp;", u"&") + m.group(3)

            html = re.sub(r'("ajax_url"\s*:\s*")([^"]*)(\")', _patch_js, html)
            html = re.sub(r"('ajax_url'\s*:\s*')([^']*)(')", _patch_js, html)
        except Exception:
            pass

        return html

    def _uquote(self, v):
        """URL 编码。"""
        v = to_unicode(v or u"")
        if not _url_quote:
            return v
        try:
            return to_unicode(_url_quote(v.encode("utf-8")))
        except Exception:
            try:
                return to_unicode(_url_quote(str(v)))
            except Exception:
                return v

    def can_manage(self):
        from Products.CMFCore.utils import getToolByName
        mt = getToolByName(self.context, "portal_membership")
        return mt.checkPermission("Modify portal content", self.context)

def _safe_int(v):
    try:
        return int(v)
    except Exception:
        return 999999