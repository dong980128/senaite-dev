# -*- coding: utf-8 -*-
"""
get_process_conditions.py

@@get-process-conditions
  单个 AR：GET process_uid + ar_uid
  返回：{"success": true, "conditions": [{title, type, choices, default, required, value}]}

@@get-batch-process-conditions
  批量 AR：GET process_uid + ar_uids（逗号分隔）
  返回：{
    "success": true,
    "fields": [{title, type, choices, required}],   <- 字段定义（只取一次）
    "samples": [                                     <- 每个样本的预填值
      {"ar_uid": "xxx", "ar_id": "WPB-0263", "values": {title: value, ...}},
      ...
    ]
  }
"""

import json
import logging

from Products.Five.browser import BrowserView
from bika.lims import api

from senaite.core.labprocess.utils_common import (
    as_list,
    to_unicode,
)

logger = logging.getLogger(__name__)

# 样本字段映射（title -> AR 方法名），与 abstractroutineanalysis 保持一致
SAMPLE_FIELD_MAP = {
    u"样本类型": "getSampleTypeTitle",
    u"癌种": "getCancerType",
    u"国籍": "getEthnicity",
    u"受试者唯一编码": "getSubjectUID",
    u"疾病诊断": "getDiagnosis",
}


def _prefill_from_ar(ar, title, default=u""):
    """从 AR 取某个字段的预填值。"""
    if not ar:
        return default
    method_name = SAMPLE_FIELD_MAP.get(title)
    if not method_name:
        return default
    try:
        method = getattr(ar, method_name, None)
        if callable(method):
            raw = method()
            if raw:
                if hasattr(raw, "Title"):
                    return to_unicode(raw.Title())
                elif isinstance(raw, (list, tuple)):
                    return u",".join([to_unicode(v) for v in raw if v])
                else:
                    return to_unicode(raw)
    except Exception as e:
        logger.warning("[get_process_conditions] prefill ar=%s title=%s: %s",
                       getattr(ar, "getId", lambda: "?")(), title, e)
    return default


def _get_pipeline_stages(process):
    from senaite.core.labprocess.utils_common import to_int
    if hasattr(process, "getPipelineStages"):
        try:
            stages = process.getPipelineStages() or []
            if isinstance(stages, (list, tuple)):
                return sorted(stages, key=lambda x: to_int((x or {}).get("step", 0)))
        except Exception:
            pass
    raw = to_unicode(getattr(process, "pipeline_stages", u"") or u"").strip()
    if not raw:
        return []
    try:
        stages = json.loads(raw)
        if not isinstance(stages, (list, tuple)):
            return []
        return sorted(stages, key=lambda x: to_int((x or {}).get("step", 0)))
    except Exception:
        return []


def _get_service_by_token(token):
    from senaite.core.labprocess.analysis_utils import get_analysisservice_by_token
    from senaite.core.labprocess.utils_common import norm_token
    return get_analysisservice_by_token(norm_token(token))


def _get_condition_defs(process):
    """从 LabProcess 第一步 service 读取 Conditions 定义，返回 list[dict]。"""
    stages = _get_pipeline_stages(process)
    if not stages:
        return []
    first_stage = stages[0]
    services = as_list(first_stage.get("services", []) or [])
    if not services:
        return []
    svc = _get_service_by_token(services[0])
    if not svc:
        return []
    try:
        if hasattr(svc, "getConditions"):
            return svc.getConditions() or []
    except Exception as e:
        logger.warning("[get_process_conditions] getConditions failed: %s", e)
    return []


class GetProcessConditionsView(BrowserView):
    """@@get-process-conditions — 单个 AR"""

    def __call__(self):
        self.request.response.setHeader("Content-Type", "application/json")

        process_uid = (self.request.get("process_uid") or "").strip()
        ar_uid = (self.request.get("ar_uid") or "").strip()

        if not process_uid:
            return self._error("process_uid is required")

        pc = api.get_tool("portal_catalog")
        brains = pc(UID=process_uid)
        if not brains:
            return self._error("LabProcess not found: %s" % process_uid)
        process = brains[0].getObject()

        ar = None
        if ar_uid:
            try:
                ar = api.get_object_by_uid(ar_uid, default=None)
            except Exception:
                ar = None

        condition_defs = _get_condition_defs(process)
        if not condition_defs:
            return json.dumps({"success": True, "conditions": []})

        conditions = []
        for cond in condition_defs:
            title = to_unicode(cond.get("title") or u"").strip()
            ctype = to_unicode(cond.get("type") or u"text").strip()
            choices = to_unicode(cond.get("choices") or u"").strip()
            default = to_unicode(cond.get("default") or u"").strip()
            required = bool(cond.get("required", False))
            value = _prefill_from_ar(ar, title, default)
            conditions.append({
                "title": title,
                "type": ctype,
                "choices": choices,
                "default": default,
                "required": required,
                "value": value,
            })

        return json.dumps({"success": True, "conditions": conditions})

    def _error(self, message):
        logger.error("[get_process_conditions] %s", message)
        return json.dumps({"success": False, "error": message})


class GetBatchProcessConditionsView(BrowserView):
    """
    @@get-batch-process-conditions — 批量 AR

    GET 参数：
      process_uid  — LabProcess UID
      ar_uids      — 逗号分隔的 AR UID 列表

    返回：
      {
        "success": true,
        "fields":  [{title, type, choices, required, description}],
        "samples": [
          {"ar_uid": "xxx", "ar_id": "WPB-0263", "values": {"字段名": "预填值", ...}},
          ...
        ]
      }
    """

    def __call__(self):
        self.request.response.setHeader("Content-Type", "application/json")

        process_uid = (self.request.get("process_uid") or "").strip()
        ar_uids_raw = (self.request.get("ar_uids") or "").strip()

        if not process_uid:
            return self._error("process_uid is required")
        if not ar_uids_raw:
            return self._error("ar_uids is required")

        # 解析 AR UID 列表
        ar_uids = [u.strip() for u in ar_uids_raw.split(",") if u.strip()]
        if not ar_uids:
            return self._error("ar_uids is empty")

        # 获取 LabProcess
        pc = api.get_tool("portal_catalog")
        brains = pc(UID=process_uid)
        if not brains:
            return self._error("LabProcess not found: %s" % process_uid)
        process = brains[0].getObject()

        # 获取字段定义
        condition_defs = _get_condition_defs(process)
        if not condition_defs:
            return json.dumps({"success": True, "fields": [], "samples": []})

        # 构建字段定义列表（只需一份）
        fields = []

        for cond in condition_defs:
            title = to_unicode(cond.get("title") or u"").strip()
            ctype = to_unicode(cond.get("type") or u"text").strip()
            choices = to_unicode(cond.get("choices") or u"").strip()
            desc = to_unicode(cond.get("description") or u"").strip()
            required = bool(cond.get("required", False))
            default = to_unicode(cond.get("default") or u"").strip()
            fields.append({
                "title": title,
                "type": ctype,
                "choices": choices,
                "description": desc,
                "required": required,
                "default": default,
            })

        # 逐个样本预填值
        samples = []
        for ar_uid in ar_uids:
            try:
                ar = api.get_object_by_uid(ar_uid, default=None)
            except Exception:
                ar = None

            ar_id = ""
            if ar:
                try:
                    ar_id = to_unicode(ar.getId() or u"")
                except Exception:
                    ar_id = ar_uid[:8]

            values = {}
            for cond in condition_defs:
                title = to_unicode(cond.get("title") or u"").strip()
                default = to_unicode(cond.get("default") or u"").strip()
                values[title] = _prefill_from_ar(ar, title, default)

            samples.append({
                "ar_uid": ar_uid,
                "ar_id": ar_id,
                "values": values,
            })

        return json.dumps({
            "success": True,
            "fields": fields,
            "samples": samples,
        })

    def _error(self, message):
        logger.error("[get_batch_process_conditions] %s", message)
        return json.dumps({"success": False, "error": message})
