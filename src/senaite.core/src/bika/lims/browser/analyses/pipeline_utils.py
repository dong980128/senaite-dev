# -*- coding: utf-8 -*-
import json
import logging
from bika.lims import api

LOG = logging.getLogger("pipeline")


def get_profiles_for_ar(ar):
    """从样本(AR)上拿到选中的套餐列表"""
    profiles = getattr(ar, "getProfiles", lambda: [])()
    profiles = api.to_list(profiles)

    try:
        ids = [getattr(p, "getId", lambda: repr(p))() for p in profiles]
    except Exception:
        ids = [repr(p) for p in profiles]

    LOG.info("[pipeline] AR=%s getProfiles -> %r", ar.getId(), ids)
    return profiles


def get_pipeline_cfg_from_profile(profile):
    pid = getattr(profile, "getId", lambda: repr(profile))()

    # 尝试多种 getter / 属性名
    raw = u""
    tried = []

    # 先尝试getter方法
    for name in ("getPipelineStagesConfiguration",
                 "getPipelineStages",
                 "getPipelineStagesConfig"):
        meth = getattr(profile, name, None)
        tried.append(name)
        if callable(meth):
            raw = meth()
            break

    # 如果还是空，再尝试直接属性名
    if not raw:
        for name in ("PipelineStagesConfiguration",
                     "PipelineStages",
                     "pipeline_stages_configuration",
                     "pipeline_stages"):
            tried.append(name)
            if hasattr(profile, name):
                raw = getattr(profile, name)
                break

    LOG.info("[pipeline] Profile=%s tried attrs=%r, raw JSON=%r",
             pid, tried, raw)

    if not raw:
        return []

    try:
        cfg = json.loads(raw)
        if not isinstance(cfg, list):
            LOG.warn("[pipeline] config for %s is not a list: %r", pid, cfg)
            return []
        LOG.info("[pipeline] parsed cfg for %s -> %r", pid, cfg)
        return cfg
    except Exception:
        LOG.exception("[pipeline] invalid JSON on %s", pid)
        return []


def get_pipeline_cfg_for_ar(ar):
    """方便函数：直接从 AR 拿到 pipeline 配置"""
    profiles = get_profiles_for_ar(ar)
    if not profiles:
        LOG.info("[pipeline] AR=%s has NO profiles", ar.getId())
        return []

    profile = profiles[0]
    LOG.info("[pipeline] AR=%s use profile=%s",
             ar.getId(), getattr(profile, "getId", lambda: repr(profile))())
    return get_pipeline_cfg_from_profile(profile)


def find_step_for_analysis(analysis):
    """给一个 analysis，算出它属于 pipeline 的第几步，以及这步的 users"""
    ar = analysis.aq_parent
    cfg = get_pipeline_cfg_for_ar(ar)
    if not cfg:
        return None, None

    svc = analysis.getAnalysisService()
    keyword = svc.getKeyword()

    for step_cfg in cfg:
        step = step_cfg.get("step")
        services = step_cfg.get("services") or []
        users = step_cfg.get("users") or []
        if keyword in services:
            return step, users

    return None, None
