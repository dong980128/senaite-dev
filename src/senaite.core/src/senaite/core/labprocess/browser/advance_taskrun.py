# -*- coding: utf-8 -*-
"""
advance_taskrun.py
处理 @@advance-taskrun 请求。

支持的 transition（通过 POST 参数 transition 传入，默认 complete）：
  complete   running -> done，创建下一个 TaskRun 或结束流程
  retract    running/done -> retracted
               - 当前 TaskRun Analysis 移到无效（reject）
               - 下游 TaskRun 撤回 Analysis 后删除对象
               - 当前步自动重建新 TaskRun + Analysis
               - ProcessRun 回到 active
  invalidate running/done -> invalid（同 retract 逻辑）

注：reinstate 已移除，retract 后自动重建新 TaskRun，无需手动 reinstate。
"""

import logging

from Products.CMFCore.utils import getToolByName
from Products.Five.browser import BrowserView
from zExceptions import BadRequest

from bika.lims import api

from senaite.core.labprocess.utils_common import (
    as_list,
    get_path,
    get_pipeline,
    get_uid,
    next_stage,
    sync_status_from_wf,
    to_int,
    to_unicode,
    wf_state,
)
from senaite.core.labprocess.utils_auth import require_post
from senaite.core.labprocess.analysis_utils import (
    cascade_analyses_transition,
    ensure_analyses_for_stage,
    ensure_workflow_initialized,
    resolve_analysis_uids_by_tokens,
    inject_tcr_data_if_needed,
    writeback_tcr_selected_to_prev,
    clearback_tcr_selected_to_prev,
)

logger = logging.getLogger(__name__)

try:
    from plone.dexterity.utils import createContentInContainer
except Exception:
    createContentInContainer = None

TRANSITION_ALLOWED_STATES = {
    "complete": ("running",),
    "retract": ("running", "done"),
    "invalidate": ("running", "done"),
    # reinstate 已移除：retract 后自动重建新 TaskRun
}

TRANSITION_FALLBACK_STATUS = {
    "complete": u"done",
    "retract": u"retracted",
    "invalidate": u"invalid",
}

TASKRUN_TERMINAL_STATES = ("retracted", "invalid", "cancelled")


# 执行简单 transition
def _do_simple_transition(tr, portal, transition):
    """执行 workflow transition，同步 status，级联处理 Analysis。"""
    wf = getToolByName(portal, "portal_workflow")
    before = wf_state(tr, portal=portal)
    fallback = TRANSITION_FALLBACK_STATUS.get(transition, u"")

    wf.doActionFor(tr, transition)
    after = wf_state(tr, portal=portal)
    sync_status_from_wf(tr, portal=portal, fallback=fallback)
    tr.reindexObject()

    cascade_analyses_transition(tr, portal, transition)


# 删除下游 TaskRun
def _delete_downstream_taskruns(run, portal, cur_step, transition):
    """
    删除 step > cur_step 的所有下游 TaskRun。
    处理顺序：step 从大到小（先删最下游）。
    每个下游 TaskRun：先撤回 Analysis，再删除对象。
    """

    downstream = []
    for obj in run.objectValues():
        if getattr(obj, "portal_type", "") != "LabTaskRun":
            continue
        step = to_int(getattr(obj, "step", 0) or 0)
        if step > cur_step:
            downstream.append((step, obj))

    downstream.sort(key=lambda x: x[0], reverse=True)

    if not downstream:
        return

    for step, downstream_tr in downstream:
        tr_uid = get_uid(downstream_tr)
        tr_id = downstream_tr.getId()

        # 先撤回 Analysis
        try:
            cascade_analyses_transition(downstream_tr, portal, transition)
        except Exception:
            logger.exception(
                "[advance_taskrun] cascade analyses failed downstream uid=%s", tr_uid
            )

        # 删除 TaskRun 对象
        try:
            run.manage_delObjects([tr_id])
        except Exception:
            logger.exception(
                "[advance_taskrun] delete downstream taskrun failed id=%s step=%s",
                tr_id, step
            )


# 为当前步重建新 TaskRun
def _rebuild_current_taskrun(run, ar, portal, cur_step, pipeline):
    """
    为当前步（cur_step）重建一个新的 TaskRun。

    id 规则：
      原始 taskrun-{step} 已被 retracted 对象占用，
      新建 taskrun-{step}-1, taskrun-{step}-2, ...
    """
    # 从 pipeline 找到当前 stage
    stage = None
    for s in (pipeline or []):
        if to_int(s.get("step", 0)) == cur_step:
            stage = s
            break

    if stage is None:
        logger.warning(
            "[advance_taskrun] stage not found for step=%s, cannot rebuild", cur_step
        )
        return None

    step = cur_step
    title = to_unicode(stage.get("name") or (u"Task %s" % step))
    mode = to_unicode(stage.get("mode", u"analysis") or u"analysis").lower()
    handler = to_unicode(stage.get("handler", u"") or u"")
    services = as_list(stage.get("services", []) or [])
    users = as_list(stage.get("users", []) or [])

    # 生成唯一 id
    base_id = "taskrun-%s" % step
    counter = 1
    new_id = "%s-%s" % (base_id, counter)
    while new_id in run.objectIds():
        counter += 1
        new_id = "%s-%s" % (base_id, counter)

    # 创建新 TaskRun
    try:
        if createContentInContainer:
            tr = createContentInContainer(
                run, "LabTaskRun",
                id=new_id, title=title,
                checkConstraints=False
            )
        else:
            tr = api.create(run, "LabTaskRun", id=new_id, title=title)
    except Exception:
        logger.exception(
            "[advance_taskrun] create new taskrun failed step=%s id=%s", step, new_id
        )
        return None

    # 设置字段
    tr.step = step
    tr.stage_name = title
    tr.task_name = title
    tr.mode = mode
    tr.handler = handler
    tr.assigned_users = users

    ensure_workflow_initialized(portal, tr)
    sync_status_from_wf(tr, portal=portal, fallback=u"running")

    try:
        tr.reindexObject()
    except Exception:
        pass

    # analysis 模式：创建新 Analysis
    if mode == u"analysis" and ar is not None:
        analyst = users[0] if users else None
        conditional_services = set(
            cs.get("service", "")
            for cs in (stage.get("conditional_services", []) or [])
            if isinstance(cs, dict) and cs.get("service")
        )
        direct_services = [s for s in services if s not in conditional_services]
        ensure_analyses_for_stage(ar, direct_services, analyst=analyst)
        inject_tcr_data_if_needed(run, ar, stage, portal)
        tr.services = direct_services
        tr.analysis_uids = resolve_analysis_uids_by_tokens(ar, direct_services)
        tr.conditional_services = [
            cs for cs in (stage.get("conditional_services", []) or [])
            if isinstance(cs, dict)
        ]

    else:
        tr.services = []
        tr.analysis_uids = []
        tr.conditional_services = []

    tr.reindexObject()
    return tr


# 完成当前 TaskRun
def _complete_taskrun(tr, portal):
    """把当前 LabTaskRun 推进到 done 状态，级联 submit Analysis。"""
    wf = getToolByName(portal, "portal_workflow")
    before = wf_state(tr, portal=portal)

    wf.doActionFor(tr, "complete")
    after = wf_state(tr, portal=portal)
    sync_status_from_wf(tr, portal=portal, fallback=u"done")
    tr.reindexObject()
    cascade_analyses_transition(tr, portal, "complete", submit_only=True)


# def _get_prev_file_uids(run, portal, cur_step):
#     """
#     从已完成的 TaskRun 里收集所有 result_type=file 的 InterimField 值。
#     返回去重后的 Attachment UID 列表，保持发现顺序。
#     （上游上传两个文件时，返回 [table1_uid, table2_uid]）
#     """
#     wf = getToolByName(portal, "portal_workflow")
#     uids = []
#     seen = set()
#     for tr_id in run.objectIds():
#         tr = run.get(tr_id)
#         if not tr or getattr(tr, "portal_type", "") != "LabTaskRun":
#             continue
#         state = wf.getInfoFor(tr, "review_state", "") or ""
#         if state != "done":
#             continue
#         for a_uid in (getattr(tr, "analysis_uids", None) or []):
#             try:
#                 analysis = api.get_object_by_uid(a_uid, default=None)
#                 if not analysis:
#                     continue
#                 for interim in (analysis.getInterimFields() or []):
#                     if interim.get("result_type") == "file":
#                         val = to_unicode(interim.get("value", "") or "").strip()
#                         if val and val not in seen:
#                             seen.add(val)
#                             uids.append(val)
#             except Exception:
#                 continue
#     return uids

# 创建下一个 LabTaskRun
def _create_next_taskrun(run, ar, stage, portal):
    """
    根据下一个 stage 创建新的 LabTaskRun。
    下游 TaskRun 已在 retract 时删除，这里直接新建。
    """
    step = to_int(stage.get("step", 0))
    title = to_unicode(stage.get("name") or (u"Task %s" % step))
    mode = to_unicode(stage.get("mode", u"analysis") or u"analysis").lower()
    handler = to_unicode(stage.get("handler", u"") or u"")
    services = as_list(stage.get("services", []) or [])
    users = as_list(stage.get("users", []) or [])

    task_id = "taskrun-%s" % step

    if task_id in run.objectIds():
        tr = run.get(task_id)
    else:
        if createContentInContainer:
            tr = createContentInContainer(
                run, "LabTaskRun",
                id=task_id, title=title,
                checkConstraints=False
            )
        else:
            tr = api.create(run, "LabTaskRun", id=task_id, title=title)

    tr.step = step
    tr.stage_name = title
    tr.task_name = title
    tr.mode = mode
    tr.handler = handler
    tr.assigned_users = users

    ensure_workflow_initialized(portal, tr)
    sync_status_from_wf(tr, portal=portal, fallback=u"running")

    try:
        tr.reindexObject()
    except Exception:
        pass

    if mode == u"analysis" and ar is not None:
        analyst = users[0] if users else None
        conditional_services = set(
            cs.get("service", "")
            for cs in (stage.get("conditional_services", []) or [])
            if isinstance(cs, dict) and cs.get("service")
        )
        direct_services = [s for s in services if s not in conditional_services]
        ensure_analyses_for_stage(ar, direct_services, analyst=analyst)
        inject_tcr_data_if_needed(run, ar, stage, portal)
        tr.services = direct_services
        tr.analysis_uids = resolve_analysis_uids_by_tokens(ar, direct_services)
        tr.conditional_services = [
            cs for cs in (stage.get("conditional_services", []) or [])
            if isinstance(cs, dict)
        ]
    else:
        tr.services = []
        tr.analysis_uids = []
        tr.conditional_services = []

    tr.reindexObject()
    return tr


class AdvanceTaskRunView(BrowserView):
    """
    @@advance-taskrun
    绑定在 LabTaskRun 上，通过 POST 触发。

    complete：完成任务，创建下一步或结束流程。
    retract/invalidate：
      1. 当前 TaskRun → retracted/invalid，Analysis → reject
      2. 删除所有下游 TaskRun（先撤回其 Analysis）
      3. 为当前步重建新 TaskRun + Analysis
      4. ProcessRun → active
    reinstate：已移除，由 retract 后自动重建替代。
    """

    def _check(self, transition):
        tr = self.context
        if getattr(tr, "portal_type", "") != "LabTaskRun":
            raise BadRequest("Not a LabTaskRun")
        require_post(self.request)
        if transition not in TRANSITION_ALLOWED_STATES:
            raise BadRequest("Unknown transition: %s" % transition)

    def __call__(self):

        transition = (
                self.request.form.get("transition")
                or self.request.get("transition")
                or "complete"
        ).strip().lower()

        self._check(transition)

        tr = self.context
        portal = api.get_portal()

        run = getattr(tr, "aq_parent", None)
        if not run or getattr(run, "portal_type", "") != "LabProcessRun":
            raise BadRequest("Parent is not LabProcessRun")

        ar = getattr(run, "aq_parent", None)
        if ar and getattr(ar, "portal_type", "") not in ("AnalysisRequest", "Sample"):
            ar = None

        def _redirect():
            referer = self.request.get("HTTP_REFERER", "")
            if referer:
                return self.request.response.redirect(referer)
            if ar and hasattr(ar, "absolute_url"):
                return self.request.response.redirect(ar.absolute_url() + "#labprocessruns")
            return self.request.response.redirect(run.absolute_url())

        if transition == "complete":
            cur_step = to_int(getattr(tr, "step", 0))

            # 先读勾选数据再 complete（Analysis 还在 assigned 状态，数据最可靠）
            writeback_tcr_selected_to_prev(run, ar, portal, cur_step)

            try:
                _complete_taskrun(tr, portal)
            except Exception:
                logger.exception("[advance_taskrun] complete failed path=%s", get_path(tr))
                return _redirect()

            pipeline = get_pipeline(run)
            stage = next_stage(pipeline, cur_step)

            if stage:
                _create_next_taskrun(run, ar, stage, portal)
                try:
                    run.status = u"active"
                    run.reindexObject()
                except Exception:
                    pass
            else:
                try:
                    run.status = u"done"
                    run.reindexObject()
                except Exception:
                    pass

        elif transition in ("retract", "invalidate"):
            cur_step = to_int(getattr(tr, "step", 0))
            pipeline = get_pipeline(run)

            # 1. 撤回当前 TaskRun + Analysis
            try:
                _do_simple_transition(tr, portal, transition)
            except Exception:
                logger.exception("[advance_taskrun] %s failed path=%s",
                                 transition, get_path(tr))
                return _redirect()

            # 2. 删除下游 TaskRun（含 Analysis 撤回）
            _delete_downstream_taskruns(run, portal, cur_step, transition)

            # 3. 清空上一步的回写数据（retract 后上游回显应清空）
            clearback_tcr_selected_to_prev(run, ar, portal, cur_step)

            # 4. 为当前步重建新 TaskRun + Analysis
            new_tr = _rebuild_current_taskrun(run, ar, portal, cur_step, pipeline)

            # 5. ProcessRun → active
            try:
                run.status = u"active"
                run.reindexObject()
            except Exception:
                logger.exception("[advance_taskrun] reset processrun status failed")

        else:
            logger.warning("[advance_taskrun] unknown transition=%s", transition)

        return _redirect()