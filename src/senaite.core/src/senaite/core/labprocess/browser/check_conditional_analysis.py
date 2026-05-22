# -*- coding: utf-8 -*-
"""
check_conditional_analysis.py

@@check-conditional-analysis
绑定在 AnalysisRequest 上，由前端 JS 在字段变化时调用。

功能：
  根据 前一个实验的字段值，决定是否创建下个任务
  例如：sc_suspension_prep_qc 的 result_build_library字段值，
  决定是否在当前 TaskRun 中创建/取消 sc_library_prep Analysis。

POST 参数：
  taskrun_uid   当前 TaskRun 的 UID
  field_value   result_build_library 的值（"y" 或 "n"）
"""

import json
import logging
import transaction
import traceback

from Products.Five.browser import BrowserView
from bika.lims import api
from bika.lims.workflow import doActionFor
from zExceptions import BadRequest

from senaite.core.labprocess.utils_common import to_unicode, get_uid
from senaite.core.labprocess.analysis_utils import get_analysisservice_by_token
from senaite.core.labprocess.analysis_utils import (
    ensure_analyses_for_stage,
    resolve_analysis_uids_by_tokens,
)
from senaite.core.labprocess.utils_common import get_uid
from senaite.core.labprocess.utils_common import wf_state
from Products.CMFCore.utils import getToolByName

logger = logging.getLogger(__name__)

# 配置已移到 pipeline_stages JSON 的 conditional_services 字段
# TaskRun 创建时从 pipeline JSON 读取并存储到 tr.conditional_services
# 格式: [{"service": "sc_library_prep", "condition_field": "result_build_library",
#          "condition_value": "y", "trigger_service": "sc_suspension_prep_qc"}]

def _get_interim_value(analysis, keyword):
    """从 Analysis 的 interim 字段里读取指定 keyword 的值"""
    try:
        interims = analysis.getInterimFields() or []
        for field in interims:
            if field.get("keyword") == keyword:
                return field.get("value", "")
    except Exception:
        pass
    return ""

def _cancel_analysis_by_service(ar, service_keyword, taskrun):
    """
    取消 TaskRun 里指定 service 的 Analysis，并从 analysis_uids 移除。
    """
    svc = get_analysisservice_by_token(service_keyword)
    if svc is None:
        logger.warning("[conditional] service not found: %s", service_keyword)
        return False

    svc_uid = get_uid(svc)
    removed = []

    for child in ar.objectValues():
        if getattr(child, "portal_type", "") != "Analysis":
            continue
        try:
            child_svc_uid = child.getServiceUID()
        except Exception:
            continue
        if child_svc_uid != svc_uid:
            continue

        # 跳过已经是终态的
        portal = api.get_portal()
        wf = getToolByName(portal, "portal_workflow")
        state = wf.getInfoFor(child, "review_state", "")
        if state in ("cancelled", "rejected", "retracted", "invalid"):
            continue

        # 尝试 cancel
        child_uid = get_uid(child)
        try:
            for action in ("cancel", "reject", "retract"):
                try:
                    doActionFor(child, action)
                    removed.append(child_uid)
                    break
                except Exception:
                    continue
        except Exception as e:
            logger.warning("[conditional] cancel failed uid=%s err=%s", child_uid[:8], e)

    # 从 TaskRun.analysis_uids 移除
    if removed:
        current_uids = list(getattr(taskrun, "analysis_uids", []) or [])
        new_uids = [u for u in current_uids if u not in removed]
        taskrun.analysis_uids = new_uids
        taskrun._p_changed = True
        taskrun.reindexObject()

    return removed  # 返回被取消的 uid 列表

def _create_analysis_for_service(ar, service_keyword, taskrun):
    """
    在 AR 下创建指定 service 的 Analysis，并追加到 TaskRun.analysis_uids。
    """
    # 检查是否已存在活跃的（避免重复创建）
    portal = api.get_portal()
    svc = get_analysisservice_by_token(service_keyword)
    if svc is None:
        logger.warning("[conditional] service not found: %s", service_keyword)
        return False

    svc_uid = get_uid(svc)
    TERMINAL = {"cancelled", "rejected", "retracted", "invalid"}

    for child in ar.objectValues():
        if getattr(child, "portal_type", "") != "Analysis":
            continue
        try:
            if child.getServiceUID() == svc_uid:
                state = wf_state(child, portal=portal)
                if state not in TERMINAL:
                    # 已存在活跃的，检查是否在 taskrun.analysis_uids
                    child_uid = get_uid(child)
                    current_uids = list(getattr(taskrun, "analysis_uids", []) or [])
                    if child_uid not in current_uids:
                        taskrun.analysis_uids = current_uids + [child_uid]
                        taskrun._p_changed = True
                        taskrun.reindexObject()
                    return True
        except Exception:
            continue

    # 创建新 Analysis
    users = list(getattr(taskrun, "assigned_users", []) or [])
    analyst = users[0] if users else None

    ensure_analyses_for_stage(ar, [service_keyword], analyst=analyst)
    new_uids = resolve_analysis_uids_by_tokens(ar, [service_keyword])

    if new_uids:
        current_uids = list(getattr(taskrun, "analysis_uids", []) or [])
        merged = current_uids + [u for u in new_uids if u not in current_uids]
        taskrun.analysis_uids = merged
        taskrun._p_changed = True
        taskrun.reindexObject()
        return True

    logger.warning("[conditional] create failed for service=%s", service_keyword)
    return False

class CheckConditionalAnalysisView(BrowserView):
    """
    @@check-conditional-analysis
    POST: taskrun_uid, field_keyword, field_value, trigger_service
    """

    def __call__(self):
        request = self.request
        response = request.response
        response.setHeader("Content-Type", "application/json")

        # 只允许 POST
        if request.get("REQUEST_METHOD", "GET").upper() != "POST":
            response.setStatus(405)
            return json.dumps({"error": "POST required"})

        # 读取参数
        taskrun_uid     = to_unicode(request.form.get("taskrun_uid", "") or "").strip()
        field_keyword   = to_unicode(request.form.get("field_keyword", "") or "").strip()
        field_value     = to_unicode(request.form.get("field_value", "") or "").strip()
        trigger_service = to_unicode(request.form.get("trigger_service", "") or "").strip()

        if not taskrun_uid:
            response.setStatus(400)
            return json.dumps({"error": "taskrun_uid required"})

        # 获取 TaskRun
        # LabTaskRun 可能未被 uid_catalog 索引，改用遍历 AR 子对象查找
        ar = self.context  # view 绑定在 AR 上
        taskrun = None
        for proc_run in ar.objectValues():
            if getattr(proc_run, "portal_type", "") != "LabProcessRun":
                continue
            for tr in proc_run.objectValues():
                if getattr(tr, "portal_type", "") != "LabTaskRun":
                    continue
                if get_uid(tr) == taskrun_uid:
                    taskrun = tr
                    break
            if taskrun is not None:
                break

        if taskrun is None:
            # fallback: 尝试 uid_catalog
            try:
                obj = api.get_object_by_uid(taskrun_uid)
                if getattr(obj, "portal_type", "") == "LabTaskRun":
                    taskrun = obj
                    ar = getattr(taskrun, "aq_parent", None)
                    ar = getattr(ar, "aq_parent", None)
            except Exception:
                pass

        if taskrun is None:
            response.setStatus(404)
            return json.dumps({"error": "taskrun not found"})

        # 从 TaskRun.conditional_services 读取配置
        # conditional_services 格式:
        # [{"service": "sc_library_prep", "condition_field": "result_build_library",
        #   "condition_value": "y", "trigger_service": "sc_suspension_prep_qc"}]
        conditional_services = list(getattr(taskrun, "conditional_services", []) or [])

        # 找到匹配当前 trigger_service + field_keyword 的配置项
        matched = [
            cs for cs in conditional_services
            if cs.get("trigger_service") == trigger_service
            and cs.get("condition_field") == field_keyword
        ]

        if not matched:
            return json.dumps({"ok": True, "action": "no_config"})

        try:
            # 判断当前值是否满足条件
            target_service = None
            services_to_cancel = []
            for cs in matched:
                svc = cs.get("service", "")
                if not svc:
                    continue
                if field_value == cs.get("condition_value", ""):
                    target_service = svc  # 条件满足，创建
                else:
                    services_to_cancel.append(svc)  # 条件不满足，取消

            cancelled_uids = []
            if target_service:
                ok = _create_analysis_for_service(ar, target_service, taskrun)
                action = "created" if ok else "create_failed"
            elif services_to_cancel:
                for svc in services_to_cancel:
                    uids = _cancel_analysis_by_service(ar, svc, taskrun)
                    if uids:
                        cancelled_uids.extend(uids if isinstance(uids, list) else [])
                action = "cancelled" if cancelled_uids else "nothing_to_cancel"
            else:
                action = "no_match"

            transaction.commit()
            result = {"ok": True, "action": action}
            if cancelled_uids:
                result["cancelled_uid"] = cancelled_uids[0]
                result["cancelled_uids"] = cancelled_uids
            return json.dumps(result)

        except Exception as e:
            logger.error("[conditional] error: %s", traceback.format_exc())
            try:
                transaction.abort()
            except Exception:
                pass
            response.setStatus(500)
            return json.dumps({"error": str(e)})