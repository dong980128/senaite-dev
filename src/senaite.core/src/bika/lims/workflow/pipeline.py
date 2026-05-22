# -*- coding:utf-8 -*-

"""
Pipeline workflow gate helpers.

Purpose:
- When an Analysis is submitted/verified, SENAITE default logic may promote
  the AnalysisRequest (AR) workflow (e.g. to to_be_verified).
- If AR is currently in an active LabProcessRun pipeline, we want to block
  such AR promotions until pipeline is finished.

Design:
- Keep this module low-dependency to avoid circular imports.
"""

import logging
from Products.CMFPlone.utils import safe_unicode

from bika.lims import api

logger = logging.getLogger(__name__)


def _lower(v):
    return safe_unicode(v or u"").strip().lower()


def _iter_process_runs(ar):
    """ LabProcessRun objects directly under AR"""
    return [
        o for o in (ar.objectValues() or [])
        if getattr(o, "portal_type", "") == "LabProcessRun"
    ]


def _iter_task_runs(run):
    """ LabTaskRun objects directly under LabProcessRun"""

    return [
        o for o in (run.objectValues() or [])
        if getattr(o, "portal_type", "") == "LabTaskRun"
    ]


def is_run_in_progress(run):
    """ Based on ILabProcessRunSchema.status"""
    st = _lower(getattr(run, "status", u"") or u"")
    return st in ("pending", "running", "active",)


def task_is_done(taskrun):
    """ Based on ILabTaskRunSchema.status"""
    st = _lower(getattr(taskrun, "status", "") or u"")
    return st == "done"


def task_mode(taskrun):
    return _lower(getattr(taskrun, "mode", u"analysis") or u"analysis")


def pipeline_blocks_ar_transition(ar, require_modes=("analysis",)):
    require_modes = None if require_modes is None else tuple(_lower(x) for x in require_modes)

    for run in _iter_process_runs(ar):
        if not is_run_in_progress(run):
            continue

        for tr in _iter_task_runs(run):
            m = task_mode(tr)
            if require_modes is not None and m not in require_modes:
                continue

            if not task_is_done(tr):
                return True

    return False


def find_taskrun_for_analysis(analysis):
    """Return (run, taskrun) if this analysis is referenced by a LabTaskRun."""
    try:
        ar = analysis.getRequest()
    except Exception:
        return (None, None)

    if not ar:
        return (None, None)

    try:
        auid = analysis.UID()
    except Exception:
        try:
            auid = api.get_uid(analysis)
        except Exception:
            auid = ""

    runs = [o for o in (ar.objectValues() or [])
            if getattr(o, "portal_type", "") == "LabProcessRun"]

    for run in runs:
        trs = [t for t in (run.objectValues() or [])
               if getattr(t, "portal_type", "") == "LabTaskRun"]
        for tr in trs:
            uids = getattr(tr, "analysis_uids", None) or []
            try:
                if auid in list(uids):
                    return (run, tr)
            except Exception:
                pass

    return (None, None)
