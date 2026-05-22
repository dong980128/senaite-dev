# -*- coding: utf-8 -*-
import logging

from bika.lims import api
from bika.lims import logger
from bika.lims import workflow as wf
from bika.lims.api import security

logger = logging.getLogger("senaite.core")

def _get_department_manager_userid(analysis):
    """从 Analysis 找到所属部门经理绑定的 user id。"""

    # 1) Analysis -> Service （优先用 getAnalysisService）
    get_service = None
    if hasattr(analysis, "getAnalysisService"):
        get_service = analysis.getAnalysisService
    elif hasattr(analysis, "getService"):
        # 旧方法，会有 DeprecationWarning，将来可以删
        get_service = analysis.getService

    svc = get_service() if callable(get_service) else None
    if not svc:
        logger.info(u"guard_verify: Analysis 没有 Service，无法判断部门经理")
        return None

    # Service -> Department（uidreferencefield）
    try:
        dept = svc.getDepartment()
    except Exception as e:
        logger.info(u"guard_verify: getDepartment() 失败: %s", e)
        return None

    if not dept:
        logger.info(u"guard_verify: Service 未设置 Department")
        return None

    # Department -> Manager（LabContact）
    get_mgr = getattr(dept, "getManager", None)
    mgr = get_mgr() if callable(get_mgr) else None
    if not mgr:
        logger.info(u"guard_verify: Department 未配置 Manager")
        return None

    # 4) Manager -> User（MemberData）
    user = getattr(mgr, "getUser", lambda: None)()
    if not user:
        logger.info(u"guard_verify: Manager 未绑定系统用户")
        return None

    # 5) 统一成 userid 字符串
    userid = None
    if hasattr(user, "getUserName"):
        userid = user.getUserName()
    elif hasattr(user, "getId"):
        userid = user.getId()
    else:
        userid = str(user)

    return userid or None

def user_is_site_manager():
    member = api.get_current_user()
    return "Manager" in member.getRoles()

