# -*- coding: utf-8 -*-
"""
utils_auth.py
权限和身份验证工具函数 —— 所有文件都从这里导入，禁止在其他文件重复定义。

分类：
  1. 请求检查     require_post
  2. 用户身份     get_username
  3. 角色检查     has_role / is_manager
  4. 对象权限     require_permission
  5. 任务权限     check_taskrun_access（替代 advance_taskrun 里的 _check 权限部分）
"""

import logging
from AccessControl import getSecurityManager
from Products.CMFCore.utils import getToolByName
from zExceptions import BadRequest, Unauthorized

from bika.lims import api
from senaite.core.labprocess.utils_common import as_list, to_unicode

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. 请求检查
# ---------------------------------------------------------------------------

def require_post(request):
    """
    确保请求是 POST，否则抛出 BadRequest。
    统一替代各文件里的手动判断 REQUEST_METHOD。
    """
    method = (request.get("REQUEST_METHOD", "GET") or "GET").upper()
    if method != "POST":
        raise BadRequest("POST required")


# ---------------------------------------------------------------------------
# 2. 用户身份
# ---------------------------------------------------------------------------

def get_username():
    """
    获取当前登录用户的 ID，失败返回空字符串。
    统一替代 advance_taskrun 里的 _get_username。
    """
    try:
        user = api.get_current_user()
        if user:
            uid = getattr(user, "getId", None)
            return to_unicode(uid() if callable(uid) else str(user)).strip()
    except Exception:
        pass

    # fallback
    try:
        portal = api.get_portal()
        mt = getToolByName(portal, "portal_membership")
        u = mt.getAuthenticatedMember()
        return to_unicode(u.getId() or "").strip()
    except Exception:
        return u""


# ---------------------------------------------------------------------------
# 3. 角色检查
# ---------------------------------------------------------------------------

def has_role(context, roles):
    """
    检查当前用户在 context 上是否拥有指定角色之一。
    roles: list/tuple，如 ['Manager', 'LabManager']
    """
    try:
        user = api.get_current_user()
        return api.user_has_roles(user, roles, obj=context)
    except Exception:
        pass

    # fallback
    try:
        portal = api.get_portal()
        mt = getToolByName(portal, "portal_membership")
        member = mt.getAuthenticatedMember()
        user_roles = member.getRolesInContext(context)
        return any(r in user_roles for r in roles)
    except Exception:
        return False


def is_manager(context):
    """
    检查当前用户是否是 Manager 或 LabManager。
    统一替代 advance_taskrun 里的 _is_manager。
    """
    return has_role(context, ["Manager", "LabManager"])


# ---------------------------------------------------------------------------
# 4. 对象权限
# ---------------------------------------------------------------------------

def require_permission(context, permission="Modify portal content"):
    """
    确保当前用户对 context 有指定权限，否则抛出 Unauthorized。
    统一替代 cancel_processrun 里的 getSecurityManager().checkPermission。
    """
    if not getSecurityManager().checkPermission(permission, context):
        raise Unauthorized("No permission: %s" % permission)


# ---------------------------------------------------------------------------
# 5. 任务权限
# ---------------------------------------------------------------------------

def check_taskrun_access(taskrun, request):
    """
    检查当前用户是否有权限操作指定 LabTaskRun。
    规则：Manager/LabManager 可以操作所有任务；
          普通用户只能操作自己被分配到的任务。

    统一替代 advance_taskrun 里 _check 中的权限部分。
    抛出 Unauthorized 表示无权限，返回 True 表示通过。
    """
    # Manager 直接放行
    if is_manager(taskrun):
        return True

    username = get_username()
    assigned = as_list(getattr(taskrun, "assigned_users", []) or [])

    if not username or username not in assigned:
        raise Unauthorized("Not assigned to this task (user=%s, assigned=%s)" % (username, assigned))

    return True