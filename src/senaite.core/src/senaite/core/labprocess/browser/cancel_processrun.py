# -*- coding: utf-8 -*-
"""
cancel_processrun.py
处理 @@cancel-processrun 请求。

主要内容：
  1. 权限校验
  2. 把 LabProcessRun 标记为 cancelled
  3. 级联把所有 LabTaskRun 标记为 retracted
  4. 级联撤销所有关联的 Analysis
  5. 跳转回 referer 或 Sample 页面

Analysis 撤销策略：
  - assigned / unassigned / to_be_verified：走 workflow reject
  - verified：setStatusOf 强制改状态为 rejected + 手动标记 IRejected + 清理 Worksheet
    （verified 状态 workflow 里没有 reject transition，不能走 doActionFor）
  - rejected / retracted / cancelled：已终态，跳过
  - 所有动作失败 fallback：setHidden(True)
"""

import logging

from DateTime import DateTime
from Products.Five.browser import BrowserView
from Products.CMFCore.utils import getToolByName
from bika.lims import api
from zope.interface import alsoProvides

# 公共工具
from senaite.core.labprocess.utils_common import (
    get_uid,
    parse_csv_uids,
    to_unicode,
    wf_state,
)
from senaite.core.labprocess.utils_auth import (
    require_post,
    require_permission,
)

logger = logging.getLogger(__name__)

# Analysis workflow id
ANALYSIS_WORKFLOW_ID = "senaite_analysis_workflow"

# Analysis 已终态，无需处理
ANALYSIS_TERMINAL_STATES = ("rejected", "retracted", "cancelled", "invalid")

# 非 verified 状态的撤销动作候选列表（按优先级）
ANALYSIS_CANCEL_ACTIONS = ("reject", "retract", "cancel", "invalidate")

class CancelProcessRunView(BrowserView):
    """
    @@cancel-processrun
    绑定在 LabProcessRun 上，通过 POST 触发。

    流程：
      POST
        -> 校验权限
        -> Run 标记 cancelled
        -> 所有 TaskRun 标记 retracted
        -> 所有关联 Analysis 执行撤销动作
        -> 跳转回 referer
    """

    def __call__(self):
        req = self.request

        # 1. 基础校验
        require_post(req)
        require_permission(self.context, "Modify portal content")

        run = self.context
        run_uid = get_uid(run)
        before_run = to_unicode(getattr(run, "status", u"") or u"").strip().lower() or u"active"

        # 2. 标记 Run 为 cancelled
        if before_run != u"cancelled":
            try:
                run.status = u"cancelled"
            except Exception:
                logger.warning(
                    "[cancel_processrun] set run.status failed run=%s",
                    run_uid, exc_info=True
                )

        # 3. 收集所有关联 Analysis UID
        analysis_uids = self._collect_analysis_uids(run)

        # 4. 撤销 Analysis（先于 TaskRun，避免 TaskRun retract 后 analysis_uids 被清除）
        cancelled_a = 0
        skipped_a = 0
        for auid in analysis_uids:
            a = self._get_obj_by_uid(auid)
            if not a:
                skipped_a += 1
                continue
            if self._try_cancel_analysis(a):
                cancelled_a += 1
            else:
                skipped_a += 1

        # 5. 撤销所有 TaskRun
        retracted_tr = self._retract_taskruns(run)

        try:
            run.reindexObject()
        except Exception:
            pass

        after_run = to_unicode(getattr(run, "status", u"") or u"").strip().lower()

        # 6. 跳转
        referer = req.get("HTTP_REFERER")
        if not referer:
            try:
                referer = run.aq_parent.absolute_url()
            except Exception:
                referer = run.absolute_url()
        return req.response.redirect(referer)

    def _collect_analysis_uids(self, run):
        """
        收集 run 下所有 TaskRun 的 analysis_uids，去重保持顺序。
        """
        seen = set()
        uids = []
        for obj in run.objectValues():
            if getattr(obj, "portal_type", "") != "LabTaskRun":
                continue
            raw = getattr(obj, "analysis_uids", None) or []
            for uid in parse_csv_uids(raw):
                if uid and uid not in seen:
                    seen.add(uid)
                    uids.append(uid)
        return uids

    def _get_obj_by_uid(self, uid):
        """
        按 UID 获取对象，依次尝试：
          1. api.get_object_by_uid（走 uid_catalog，最快）
          2. senaite_catalog_analysis
          3. portal_catalog
        """
        uid = to_unicode(uid).strip()
        if not uid:
            return None

        try:
            return api.get_object_by_uid(uid)
        except Exception:
            pass

        for catalog_name in ("senaite_catalog_analysis", "portal_catalog"):
            try:
                cat = api.get_tool(catalog_name)
                brains = cat(UID=uid)
                if brains:
                    return brains[0].getObject()
            except Exception:
                pass

        return None

    def _try_cancel_analysis(self, analysis):
        """
        撤销单个 Analysis。

        策略：
          1. 已终态（rejected/retracted/cancelled/invalid）-> 跳过
          2. verified -> setStatusOf 强制改为 rejected + 手动补副作用
          3. 其他状态 -> 走 workflow，按优先级尝试 reject/retract/cancel/invalidate
          4. 全部失败 fallback -> setHidden(True)

        返回 True 表示处理成功。
        """
        auid = get_uid(analysis)
        state = to_unicode(wf_state(analysis) or u"").strip().lower()

        # 1. 已终态，跳过
        if state in ANALYSIS_TERMINAL_STATES:
            return True

        # 2. verified -> 强制改状态，不走 workflow
        if state == "verified":
            return self._force_reject_verified_analysis(analysis, auid)

        # 3. 其他状态 -> 走 workflow
        wf_tool = api.get_tool("portal_workflow")
        try:
            transitions = wf_tool.getTransitionsFor(analysis) or []
            allowed = set(t.get("id") for t in transitions if isinstance(t, dict))
        except Exception:
            allowed = set()

        for cand in ANALYSIS_CANCEL_ACTIONS:
            if cand not in allowed:
                continue
            try:
                wf_tool.doActionFor(analysis, cand)
                try:
                    analysis.reindexObject()
                except Exception:
                    pass
                return True
            except Exception:
                logger.warning(
                    "[cancel_processrun] analysis uid=%s action=%s failed",
                    auid, cand, exc_info=True
                )

        # 4. fallback：setHidden(True)
        return self._fallback_hide_analysis(analysis, auid, allowed)

    def _force_reject_verified_analysis(self, analysis, auid):
        """
        verified 状态的 Analysis 无法通过 workflow reject（没有该 transition），
        使用 setStatusOf 强制修改 review_state，并手动补上关键副作用：
          - 标记 IRejected 接口
          - 清理 Worksheet 关联
          - reindex

        不触发 AR 级联（cancel 语义上整个流程已作废，AR 状态由上层决定）。
        """

        try:
            wf_tool = api.get_tool("portal_workflow")
            try:
                actor = api.get_current_user().getId()
            except Exception:
                actor = "system"

            # 强制修改 workflow_history（setStatusOf 写历史记录）
            wf_tool.setStatusOf(
                ANALYSIS_WORKFLOW_ID,
                analysis,
                {
                    "review_state": "rejected",
                    "action": "reject",
                    "actor": actor,
                    "comments": "Cancelled by LabProcessRun cancel",
                    "time": DateTime(),
                }
            )

            # 直接修改 workflow_history 最后一条确保 review_state 正确写入
            # senaite_catalog_analysis 索引时通过 workflow_history 读取状态
            try:
                wf_history = analysis.workflow_history
                if ANALYSIS_WORKFLOW_ID in wf_history:
                    history_list = list(wf_history[ANALYSIS_WORKFLOW_ID])
                    last_entry = dict(history_list[-1])
                    last_entry["review_state"] = "rejected"
                    history_list[-1] = last_entry
                    wf_history[ANALYSIS_WORKFLOW_ID] = tuple(history_list)
                    analysis.workflow_history = wf_history
                    analysis._p_changed = True
            except Exception:
                logger.warning(
                    "[cancel_processrun] patch workflow_history failed uid=%s",
                    auid, exc_info=True
                )

            # 手动标记 IRejected
            try:
                from bika.lims.interfaces import IRejected
                alsoProvides(analysis, IRejected)
            except Exception:
                logger.warning(
                    "[cancel_processrun] alsoProvides IRejected failed uid=%s",
                    auid, exc_info=True
                )

            # 清理 Worksheet 关联
            try:
                self._remove_from_worksheet(analysis)
            except Exception:
                logger.warning(
                    "[cancel_processrun] remove_from_worksheet failed uid=%s",
                    auid, exc_info=True
                )

            try:
                api.catalog_object(analysis)
            except Exception:
                pass

            return True

        except Exception:
            logger.warning(
                "[cancel_processrun] force reject verified analysis uid=%s failed",
                auid, exc_info=True
            )
            return self._fallback_hide_analysis(analysis, auid, {"verified"})

    def _remove_from_worksheet(self, analysis):
        """
        从 Worksheet 中移除 Analysis（如果有关联）。
        对应 events.py 里的 remove_analysis_from_worksheet。
        """

        worksheet = None
        try:
            worksheet = analysis.getWorksheet()
        except Exception:
            pass

        if not worksheet:
            return

        try:
            from bika.lims.workflow import doActionFor
            analyses = [an for an in worksheet.getAnalyses() if an != analysis]
            worksheet.setAnalyses(analyses)
            worksheet.purgeLayout()

            if analyses:
                doActionFor(worksheet, "submit")
                doActionFor(worksheet, "verify")
            else:
                doActionFor(worksheet, "rollback_to_open")
            worksheet.reindexObject()
        except Exception:
            logger.warning(
                "[cancel_processrun] worksheet cleanup failed uid=%s",
                get_uid(analysis), exc_info=True
            )

    def _fallback_hide_analysis(self, analysis, auid, allowed):
        """
        所有 workflow 动作都失败时的最终兜底：setHidden(True)。
        """
        try:
            if hasattr(analysis, "setHidden"):
                analysis.setHidden(True)
            else:
                analysis.Hidden = True
            try:
                analysis.reindexObject()
            except Exception:
                pass

            return True
        except Exception:
            logger.warning(
                "[cancel_processrun] analysis uid=%s fallback hide failed",
                auid, exc_info=True
            )
            return False

    def _retract_taskruns(self, run):
        """
        把 run 下所有 LabTaskRun 通过 workflow 推进到 retracted 状态。
        走 workflow 失败时 fallback 直接写 status 字段。
        返回处理数量。
        """
        portal = api.get_portal()
        wf_tool = getToolByName(portal, "portal_workflow")
        count = 0

        for obj in run.objectValues():
            if getattr(obj, "portal_type", "") != "LabTaskRun":
                continue

            current = wf_state(obj, portal=portal).lower()

            # 已经是终态则跳过
            if current == u"retracted":
                count += 1
                continue

            # 优先走 workflow
            wf_ok = False
            if current in (u"running", u"done"):
                try:
                    wf_tool.doActionFor(obj, "retract")
                    sync_status = to_unicode(
                        wf_state(obj, portal=portal) or u"retracted"
                    ).lower()
                    obj.status = sync_status
                    wf_ok = True

                except Exception:
                    logger.warning(
                        "[cancel_processrun] retract workflow failed uid=%s, fallback",
                        get_uid(obj), exc_info=True
                    )

            # fallback：直接写 status
            if not wf_ok:
                try:
                    obj.status = u"retracted"
                except Exception:
                    logger.warning(
                        "[cancel_processrun] set taskrun.status failed uid=%s",
                        get_uid(obj), exc_info=True
                    )

            try:
                obj.reindexObject()
            except Exception:
                pass
            count += 1

        return count