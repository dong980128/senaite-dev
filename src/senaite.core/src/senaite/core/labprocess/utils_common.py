# -*- coding: utf-8 -*-
"""
utils_common.py
公共基础工具函数 —— 所有文件都从这里导入，禁止在其他文件重复定义。

分类：
  1. 类型转换     to_int / to_unicode / as_list
  2. 字符串处理   norm_token / norm_step
  3. 对象工具     get_uid / get_path
  4. 工作流工具   wf_state / sync_status_from_wf
  5. Pipeline工具 get_pipeline / stage_map_by_step / next_stage
  6. Request工具  req_get / parse_csv_uids
"""

import json
import logging
from Products.CMFCore.utils import getToolByName
from bika.lims import api

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. 类型转换
# ---------------------------------------------------------------------------

def to_int(v, default=0):
    """安全转 int，失败返回 default。"""
    try:
        return int(v)
    except Exception:
        return default


def to_unicode(v, default=u""):
    """安全转 unicode。"""
    try:
        return api.safe_unicode(v)
    except Exception:
        try:
            return unicode(v)
        except Exception:
            return default


def as_list(v):
    """
    统一把 list/tuple/set/逗号字符串/None 变成 list[unicode]。
    例：'a, b' -> [u'a', u'b']
        ['a','b'] -> [u'a', u'b']
    """
    if v is None:
        return []
    if isinstance(v, (list, tuple, set)):
        out = []
        for x in v:
            s = to_unicode(x).strip()
            if s:
                out.append(s)
        return out
    if isinstance(v, basestring):
        s = to_unicode(v).strip()
        if not s:
            return []
        if "," in s:
            return [p.strip() for p in s.split(",") if p.strip()]
        return [s]
    s = to_unicode(v).strip()
    return [s] if s else []


# ---------------------------------------------------------------------------
# 2. 字符串处理
# ---------------------------------------------------------------------------

def norm_token(s):
    """
    规范化关键词：小写、去空格、下划线转中划线。
    用于 AnalysisService keyword 匹配。
    """
    s = to_unicode(s).strip().lower()
    s = s.replace(u"_", u"-")
    return s


def norm_step(step):
    """step 统一成字符串整数，例如 '1'、'2'。"""
    return to_unicode(to_int(step, 0))


# ---------------------------------------------------------------------------
# 3. 对象工具
# ---------------------------------------------------------------------------

def get_uid(obj):
    """
    安全获取对象 UID，失败返回空字符串。
    统一替代各文件里的 _uid / _safe_uid。
    """
    if obj is None:
        return u""
    try:
        return to_unicode(obj.UID())
    except Exception:
        pass
    try:
        return to_unicode(api.get_uid(obj))
    except Exception:
        return u""


def get_path(obj):
    """
    安全获取对象物理路径字符串，失败返回空字符串。
    统一替代各文件里的 _safe_path。
    """
    try:
        return "/".join(obj.getPhysicalPath())
    except Exception:
        return u""


# ---------------------------------------------------------------------------
# 4. 工作流工具
# ---------------------------------------------------------------------------

def wf_state(obj, portal=None):
    """
    获取对象当前 review_state。
    统一替代各文件里的 _wf_state。
    """
    try:
        if portal is None:
            portal = api.get_portal()
        wf = getToolByName(portal, "portal_workflow")
        return wf.getInfoFor(obj, "review_state", default="") or u""
    except Exception:
        return to_unicode(getattr(obj, "review_state", u"") or u"")


def sync_status_from_wf(obj, portal=None, fallback=u"pending"):
    """
    把 review_state 同步写到 obj.status 字段。
    统一替代各文件里的 _syc_status_from_review_state。

    返回实际写入的 status 字符串。
    """
    state = wf_state(obj, portal=portal)
    state = to_unicode(state).lower().strip()
    if not state:
        state = to_unicode(fallback).lower().strip()
    try:
        obj.status = state
    except Exception:
        logger.warning("[utils_common] sync_status_from_wf: cannot set status on %s", get_path(obj))
    return state


# ---------------------------------------------------------------------------
# 5. Pipeline 工具
# ---------------------------------------------------------------------------

def get_pipeline(run):
    """
    兼容 run.getPipeline() / run.pipeline_json 两种存储方式，
    返回 list[dict]。
    """
    if hasattr(run, "getPipeline"):
        try:
            p = run.getPipeline() or []
            if isinstance(p, (list, tuple)):
                return list(p)
        except Exception:
            pass
    raw = to_unicode(getattr(run, "pipeline_json", u"") or u"").strip()
    if not raw:
        return []
    try:
        p = json.loads(raw)
        return p if isinstance(p, list) else []
    except Exception:
        return []


def stage_map_by_step(pipeline):
    """
    pipeline(list[dict]) -> { '1': stage_dict, '2': stage_dict, ... }
    方便按 step 快速查找 stage。
    """
    m = {}
    for st in pipeline or []:
        step = norm_step((st or {}).get("step", 0))
        if step:
            m[step] = st
    return m


def next_stage(pipeline, current_step):
    """
    按 step 升序排列后，返回 current_step 之后的第一个 stage。
    没有下一步则返回 None。
    """
    cur = to_int(current_step, 0)
    stages = sorted((pipeline or []), key=lambda x: to_int((x or {}).get("step", 0)))
    for st in stages:
        if to_int((st or {}).get("step", 0)) > cur:
            return st
    return None


# ---------------------------------------------------------------------------
# 6. Request 工具
# ---------------------------------------------------------------------------

def req_get(request, key, default=u""):
    """兼容 request.form / request.get 两种取值方式。"""
    try:
        v = request.form.get(key, None)
    except Exception:
        v = None
    if v is None:
        try:
            v = request.get(key, None)
        except Exception:
            v = None
    if v is None:
        return default
    return v


def parse_csv_uids(raw):
    """
    把 'uid1,uid2,uid3' 或 list 统一转为 list[unicode]。
    用于解析前端传来的 UID 列表。
    """
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        return [to_unicode(x).strip() for x in raw if to_unicode(x).strip()]
    s = to_unicode(raw).strip()
    if not s:
        return []
    return [p.strip() for p in s.split(",") if p.strip()]
