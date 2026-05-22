# -*- coding: utf-8 -*-

import json
import logging
from Products.Five.browser import BrowserView
from zExceptions import BadRequest
from bika.lims import api
from senaite.core.labprocess.utils_common import (
    as_list, get_uid, sync_status_from_wf, to_int, to_unicode,
)
from senaite.core.labprocess.utils_auth import require_post
from senaite.core.labprocess.analysis_utils import (
    ensure_analyses_for_stage, ensure_workflow_initialized,
    resolve_analysis_uids_by_tokens,
)

logger = logging.getLogger(__name__)

try:
    from plone.dexterity.utils import createContentInContainer
except Exception:
    createContentInContainer = None

def _find_run_for_process(ar, process_uid, base_run_id):
    process_uid_u = to_unicode(process_uid)
    try:
        runs = list(ar.objectValues("LabProcessRun"))
    except Exception:
        runs = [o for o in ar.objectValues()
                if getattr(o, "portal_type", None) == "LabProcessRun"]
    for r in runs:
        tu = to_unicode(getattr(r, "template_uid", u"") or u"")
        if tu == process_uid_u:
            return r
    try:
        r = ar.get(base_run_id)
        if r and getattr(r, "portal_type", None) == "LabProcessRun":
            return r
    except Exception:
        pass
    prefix = base_run_id + "-"
    for r in runs:
        rid = r.getId() if callable(getattr(r, "getId", None)) else ""
        if rid == base_run_id or rid.startswith(prefix):
            return r
    return None

def _get_pipeline_stages(process):
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
    except Exception as e:
        logger.error("[create_processrun] pipeline_stages JSON parse failed: %s", e)
        return []

def _create_or_reuse_run(ar, process, process_uid, stages):
    run_title = u"%s - Run" % to_unicode(
        process.Title() or getattr(process, "title", u"LabProcess"))
    base_run_id = "processrun-%s" % api.to_utf8(process_uid)[-6:]
    existing = _find_run_for_process(ar, process_uid, base_run_id)
    if existing is not None:
        existing_status = to_unicode(getattr(existing, "status", u"") or u"").strip().lower()
        if existing_status not in (u"active",):
            existing = None
    run = existing
    if run is None:
        new_run_id = base_run_id
        counter = 1
        while new_run_id in ar.objectIds():
            new_run_id = "%s-%d" % (base_run_id, counter)
            counter += 1
        if createContentInContainer:
            run = createContentInContainer(ar, "LabProcessRun", id=new_run_id, title=run_title, checkConstraints=False)
        else:
            run = api.create(ar, "LabProcessRun", id=new_run_id, title=run_title)
        run.template_uid = to_unicode(process_uid)
        run.ar_uid = get_uid(ar)
        run.sample_uid = get_uid(ar)
        run.status = u"active"
    else:
        try:
            run.setTitle(run_title)
        except Exception:
            try:
                run.title = run_title
            except Exception:
                pass
    if hasattr(run, "setPipeline"):
        run.setPipeline(stages)
    else:
        run.pipeline_json = to_unicode(json.dumps(stages, ensure_ascii=False))
    portal = api.get_portal()
    ensure_workflow_initialized(portal, run)
    run.reindexObject()
    return run

def _set_analysis_conditions(ar, taskrun, condition_values):
    """把用户填写的请检信息写入 Analysis Conditions。"""
    if not condition_values:
        return
    analysis_uids = as_list(getattr(taskrun, "analysis_uids", None) or [])
    if not analysis_uids:
        logger.warning("[create_processrun] no analysis_uids on taskrun, skip conditions")
        return
    value_map = {}
    for cv in (condition_values or []):
        title = to_unicode((cv or {}).get("title") or u"").strip()
        value = to_unicode((cv or {}).get("value") or u"")
        if title:
            value_map[title] = value
    if not value_map:
        return
    for a_uid in analysis_uids:
        try:
            analysis = api.get_object_by_uid(a_uid, default=None)
            if not analysis:
                continue
            if not hasattr(analysis, "getConditions") or not hasattr(analysis, "setConditions"):
                continue
            try:
                current = analysis.getConditions(empties=True) or []
            except Exception:
                current = []
            if not current:
                continue
            updated = []
            for cond in current:
                cond = dict(cond)
                title = to_unicode(cond.get("title") or u"").strip()
                if title in value_map:
                    cond["value"] = to_unicode((value_map[title]))
                updated.append(cond)
            analysis.setConditions(updated)
            analysis.reindexObject()
        except Exception:
            logger.exception("[create_processrun] set conditions failed uid=%s", a_uid)

def _create_first_taskrun(run, ar, stage):
    step = to_int(stage.get("step", 0))
    title = to_unicode(stage.get("name") or (u"Task %s" % step))
    mode = to_unicode(stage.get("mode", u"analysis") or u"analysis").lower()
    handler = to_unicode(stage.get("handler", u"") or u"")
    services = as_list(stage.get("services", []) or [])
    users = as_list(stage.get("users", []) or [])
    task_id = "taskrun-%s" % step
    run_uid = get_uid(run)
    if task_id in run.objectIds():
        tr = run.get(task_id)
    else:
        if createContentInContainer:
            tr = createContentInContainer(run, "LabTaskRun", id=task_id, title=title, checkConstraints=False)
        else:
            tr = api.create(run, "LabTaskRun", id=task_id, title=title)
    tr.step = step
    tr.stage_name = title
    tr.task_name = title
    tr.mode = mode
    tr.handler = handler
    tr.assigned_users = users
    tr.processrun_uid = to_unicode(run_uid)
    if mode == u"analysis" and ar is not None:
        analyst = users[0] if users else None
        conditional_services = set(
            cs.get("service", "") for cs in (stage.get("conditional_services", []) or [])
            if isinstance(cs, dict) and cs.get("service"))
        direct_services = [s for s in services if s not in conditional_services]
        ensure_analyses_for_stage(ar, direct_services, analyst=analyst)
        tr.services = direct_services
        tr.analysis_uids = resolve_analysis_uids_by_tokens(ar, direct_services)
        tr.conditional_services = [cs for cs in (stage.get("conditional_services", []) or []) if isinstance(cs, dict)]
    else:
        tr.services = []
        tr.analysis_uids = []
        tr.conditional_services = []
    portal = api.get_portal()
    ensure_workflow_initialized(portal, tr)
    sync_status_from_wf(tr, portal=portal, fallback=u"running")
    tr.reindexObject()
    tr.reindexObjectSecurity()
    return tr

class CreateLabProcessRunView(BrowserView):
    """@@create-labprocessrun"""

    def __call__(self):
        req = self.request
        ar = self.context
        require_post(req)

        process_uid = req.form.get("process_uid") or req.get("process_uid")
        if not process_uid:
            raise BadRequest("Missing process_uid")

        # 读取请检信息（可选）
        condition_values_raw = req.form.get("condition_values") or req.get("condition_values") or ""
        condition_values = []
        if condition_values_raw:
            try:
                parsed = json.loads(condition_values_raw) if isinstance(condition_values_raw, str) else condition_values_raw
                if isinstance(parsed, list):
                    condition_values = parsed
            except Exception as e:
                logger.warning("[create_processrun] parse condition_values failed: %s", e)

        pc = api.get_tool("portal_catalog")
        brains = pc(UID=process_uid)
        if not brains:
            raise BadRequest("LabProcess not found: UID=%s" % process_uid)
        process = brains[0].getObject()

        stages = _get_pipeline_stages(process)
        if not stages:
            raise BadRequest("Pipeline stages is empty")

        run = _create_or_reuse_run(ar, process, process_uid, stages)
        first_stage = stages[0]
        tr = _create_first_taskrun(run, ar, first_stage)

        # 写入请检信息
        if condition_values:
            _set_analysis_conditions(ar, tr, condition_values)

        referer = req.get("HTTP_REFERER", "")
        if referer:
            return req.response.redirect(referer)
        return req.response.redirect(ar.absolute_url() + "#labprocessruns")