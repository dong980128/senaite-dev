# -*- coding: utf-8 -*-
"""
workflow/labtaskrun/guards.py
LabTaskRun workflow transition guard functions.

路径必须与 definition.xml 里的 guard-expression 一致：
  python:modules['bika.lims.workflow.labtaskrun.guards'].guard_complete(here)
"""
import logging

from senaite.core.labprocess.utils_auth import get_username, is_manager
from senaite.core.labprocess.utils_common import as_list, wf_state


def _is_assigned(taskrun):
    """检查当前用户是否在 taskrun 的 assigned_users 里。"""
    user = get_username()
    assigned = as_list(getattr(taskrun, "assigned_users", None) or [])
    return bool(user and user in assigned)


def _path(taskrun):
    try:
        return "/".join(taskrun.getPhysicalPath())
    except Exception:
        return repr(taskrun)


# ---------------------------------------------------------------------------
# Guard functions
# ---------------------------------------------------------------------------

def guard_complete(taskrun):
    """Complete：running 状态，且是 Manager/LabManager 或被分配用户。"""
    state = wf_state(taskrun)
    mgr = is_manager(taskrun)
    assigned = _is_assigned(taskrun)
    ok = (state == u"running") and (mgr or assigned)
    return ok


def guard_retract(taskrun):
    """Retract：running 或 done 状态，且是 Manager/LabManager。"""
    state = wf_state(taskrun)
    mgr = is_manager(taskrun)
    ok = (state in (u"running", u"done")) and mgr
    return ok


def guard_invalidate(taskrun):
    """Invalidate：running 或 done 状态，且是 Manager/LabManager。"""
    state = wf_state(taskrun)
    mgr = is_manager(taskrun)
    ok = (state in (u"running", u"done")) and mgr
    return ok


def guard_reinstate(taskrun):
    """Reinstate：retracted 或 invalid 状态，且是 Manager/LabManager。"""
    state = wf_state(taskrun)
    mgr = is_manager(taskrun)
    ok = (state in (u"retracted", u"invalid")) and mgr

    return ok