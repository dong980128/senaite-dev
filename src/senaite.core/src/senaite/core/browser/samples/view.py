# -*- coding: utf-8 -*-
#
# This file is part of SENAITE.CORE.
#
# SENAITE.CORE is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, version 2.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc., 51
# Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# Copyright 2018-2025 by it's authors.
# Some rights reserved, see README and LICENSE.
import re
import os

import collections
import logging
from string import Template

from bika.lims import _
from bika.lims import api
from bika.lims.api.security import check_permission
from bika.lims.config import PRIORITIES
from bika.lims.interfaces import IBatch, IAnalysisRequest
from bika.lims.interfaces import IClient
from bika.lims.utils import get_image, get_progress_bar_html
from bika.lims.utils import get_link_for
from bika.lims.utils import get_samples_filter_link
from bika.lims.utils import get_subject_link
from bika.lims.utils import get_project_link
from bika.lims.utils import getUsers
from bika.lims.workflow.analysis.test import _get_department_manager_userid
from DateTime import DateTime
from plone.memoize import view
from Products.CMFCore.utils import getToolByName
from Products.CMFDiffTool.utils import safe_unicode
from senaite.app.listing import ListingView
from senaite.core.api import dtime
from senaite.core.catalog import SAMPLE_CATALOG
from senaite.core.i18n import translate as t
from senaite.core.interfaces import ISamples
from senaite.core.interfaces import ISamplesView
from senaite.core.permissions import AddAnalysisRequest
from senaite.core.permissions import TransitionSampleSample
from senaite.core.permissions.worksheet import can_add_worksheet
from zope.interface import implementer
from plone import api as plone_api
from collections import defaultdict

logger = logging.getLogger('senaite.samples.view')


def _u(v):
    try:
        return safe_unicode(v)
    except Exception:
        try:
            return unicode(v)
        except Exception:
            return u"<unicode-error>"


def _log_debug(msg, *args):
    # 保证所有参数 unicode 安全
    args = tuple(_u(a) for a in args)
    logger.debug(msg, *args)


def _log_info(msg, *args):
    args = tuple(_u(a) for a in args)
    logger.info(msg, *args)


def _log_warn(msg, *args):
    args = tuple(_u(a) for a in args)
    logger.warning(msg, *args)


def _log_exc(msg, *args):
    args = tuple(_u(a) for a in args)
    logger.exception(msg, *args)


def _lowerish(v):
    try:
        v = v() if callable(v) else v
    except Exception:
        pass
    try:
        return safe_unicode(v or u"").lower()
    except Exception:
        return u""


ANALYSES_NUM_TPL = Template("$not_submitted/$to_be_verified/$verified/$total")
ANALYSES_NUM_TPL_HTML = Template("""<div class="d-flex flex-row">
  <span data-toggle="tooltip"
        title="$not_submitted_title"
        class="text-secondary cursor-pointer">
    $not_submitted
  </span>
  <span class="separator">/</span>
  <span data-toggle="tooltip"
        title="$to_be_verified_title"
        class="text-state-to_be_verified cursor-pointer">
    $to_be_verified
  </span>
  <span class="separator">/</span>
  <span data-toggle="tooltip"
        title="$verified_title"
        class="text-state-verified cursor-pointer">
    $verified
  </span>
  <span class="separator">/</span>
  <span data-toggle="tooltip"
        title="$total_title"
        class="text-black cursor-pointer">
    $total
  </span>
</div>
""")

# ===== Owner 计算相关的安全工具 =====
STATE_OWNER_OVERRIDES = {
    # 未接收
    'sample_due': 'mengge',
}

LABMANAGER_USER = 'liujingchao'

# ===== Owner 计算所用到的“候选字段”与“进行中状态” =====
OWNER_PROBE_ATTRS = (
    'getAnalyst', 'AssignedAnalyst',
)

# 按照站点里判定“进行中”的状态集合（如需调整，直接改这里）
RUNNING_STATES = {
    "sample_received", "to_be_verified", "verified",
    "to_be_published", "published", "in_progress", "assigned",
}


def _labmanager_display(context):
    """返回 LabManager 对应账号的人性化显示（优先 fullname）"""
    return _uid_to_display(LABMANAGER_USER, context) or u"LabManager"


def username_display_name(username):
    if not username:
        return ""
    user = api.user.get(username=username)
    if not user:
        return username
    fullname = (user.getProperty("fullname", "") or "").strip()
    return fullname or username


def _wf_state_id(obj):
    """返回对象当前工作流状态 id；失败时返回空字符串"""

    if api:
        try:
            st = api.content.get_state(obj=obj) or ""
            return safe_unicode(st)
        except Exception:
            pass

    # 直读属性
    try:
        st = getattr(obj, "review_state", None)
        if st:
            return safe_unicode(st)
    except Exception:
        pass

    # workflow 工具
    if getToolByName:
        try:
            wf = getToolByName(obj, "portal_workflow")
            st = wf.getInfoFor(obj, "review_state", default="")
            return safe_unicode(st or "")
        except Exception:
            pass
    return u""


def _read_attr_value(obj, attr):
    """尽量把字段/方法/关系对象解析成字符串ID/UID"""
    try:
        val = getattr(obj, attr, None)
        if callable(val):
            val = val()
        if not val:
            return None

        # 列表/元组：取第一个有效项
        if isinstance(val, (list, tuple)):
            val = next((x for x in val if x), None)
            if not val:
                return None

        if isinstance(val, dict):
            ids = val.get("ids") if 'ids' in val else None
            if ids:
                return safe_unicode(ids[0])
            return None

        # 对象 → 优先取UID，其次 getId/id
        try:
            from bika.lims import api as _api
        except Exception:
            _api = None

        # api.get_uid
        if _api:
            try:
                uid = _api.get_uid(val)
                if uid:
                    return safe_unicode(uid)
            except Exception:
                pass

        # Archetypes / Dexterity 常见取法
        for getter in ("UID", "getUID", "getId"):
            try:
                g = getattr(val, getter, None)
                if callable(g):
                    out = g()
                else:
                    out = g
                if out:
                    return safe_unicode(out)
            except Exception:
                pass

        if hasattr(val, "id"):
            return safe_unicode(val.id)

        # 原始字符串/数值
        return safe_unicode(val)
    except Exception:
        return None


def _probe_one_analysis(ana_obj, ar_id):
    """
    对单个 Analysis 做“字段探测”，并把命中的值打印到日志；
    命中优先顺序：按 OWNER_PROBE_ATTRS 顺序；
    命中即返回 userid，否则返回 None
    """
    ana_id = getattr(ana_obj, "id", None) or getattr(ana_obj, "getId", lambda: "?")()
    for attr in OWNER_PROBE_ATTRS:
        uid = _read_attr_value(ana_obj, attr)
        if uid:
            # _log_info(u"[OWNER][PROBE] AR=%s analysis=%s %s -> %s", ar_id, ana_id, attr, uid)
            return uid  # 命中就返回
    # _log_warn(u"[OWNER][PROBE] AR=%s analysis=%s no assignee field hit", ar_id, ana_id)
    return None


def _get_state_id(obj):
    """安全地取对象的工作流状态 id（如 sample_due / published 等）"""
    # plone.api 优先
    if plone_api:
        try:
            sid = plone_api.content.get_state(obj=obj)
            if sid:
                return sid
        except Exception as e:
            _log_debug(u"[SamplesView] plone_api.content.get_state failed: %r", e)

    # 回退：对象自带 review_state / state_title / workflow_history
    try:
        if hasattr(obj, 'review_state'):
            return obj.review_state
    except Exception:
        pass

    try:
        wf_tool = obj.portal_workflow
        sid = wf_tool.getInfoFor(obj, 'review_state', default=None)
        return sid or u""
    except Exception:
        return u""


# ========== 用户显示名 ==========
def _get_display_name_by_userid(userid):
    """把 userid 转换为显示名(fullname 优先)，失败就回退 userid"""
    userid = safe_unicode(userid or u"")
    if not userid:
        return u""

    # 1) 优先 plone.api
    try:
        user = plone_api.user.get(username=userid)
        if user:
            fn = user.getProperty("fullname", "") or ""
            fn = safe_unicode(fn)
            if fn.strip():
                return fn
    except Exception:
        pass

    # 2) 回退 bika.lims.api
    try:
        user = api.user.get(username=userid)
        if user:
            fn = user.getProperty("fullname", "") or ""
            fn = safe_unicode(fn)
            if fn.strip():
                return fn
    except Exception:
        pass

    return userid


def _iter_analyses_for_ar(ar_obj):
    # 先把 brain -> object，再判断是否 Analysis
    def _yield_if_analysis(o):
        try:
            # 不管有没有 portal_type，都优先解引用
            if hasattr(o, "getObject"):
                o = o.getObject()
        except Exception:
            return
        pt = getattr(o, "portal_type", None)
        if pt == "Analysis":
            yield o

    for name in ("getAnalyses", "getAnalysesFull", "analyses", "get_analyses"):
        fn = getattr(ar_obj, name, None)
        if callable(fn):
            try:
                res = fn()
                if res:
                    # 这里可能是对象列表，也可能是 brain 列表；都丢给 _yield_if_analysis 统一处理
                    for a in res:
                        for x in _yield_if_analysis(a):
                            yield x
                    return
            except Exception:
                _log_exc(u"[OWNER] get analyses via %s failed", name)

    # 退路：catalog 反查
    if getToolByName:
        try:
            cat = getToolByName(ar_obj, "portal_catalog")
            brains = cat(portal_type="Analysis",
                         path="/".join(ar_obj.getPhysicalPath()))
            for b in brains:
                for x in _yield_if_analysis(b):
                    yield x
        except Exception:
            _log_exc(u"[OWNER] catalog fallback for analyses failed")


def _analysis_assignee(ana_obj, ar_id):
    """
    返回 Analysis 层面的负责人（userid）；先做“快速读取”，不命中再做“字段探测”
    """
    # 先试几个最常见的
    if getattr(ana_obj, "portal_type", None) != "Analysis":
        return None

    for attr in ("getAnalyst", "Analyst", "analyst", "getAssignedAnalyst", "AssignedAnalyst"):
        uid = _read_attr_value(ana_obj, attr)
        if uid:
            # _log_info(u"[OWNER] AR=%s analysis=%s %s=%s",
            #           ar_id, getattr(ana_obj, "id", "?"), attr, uid)
            return uid

    # 快速读取未命中 → 全量探测
    return _probe_one_analysis(ana_obj, ar_id)


def _is_analysis_in_progress(ana):
    """判断该分析是否处于进行中；可按需扩展"""
    try:
        sid = _read_attr_value(ana, "review_state") or _read_attr_value(ana, "getReviewState")
        sid = (sid or "").strip()
        return sid in RUNNING_STATES or not sid  # 没有状态也暂时算进行中
    except Exception:
        return True


def _modified_dt(obj):
    """获取对象修改时间，排序用"""
    for attr in ("modified", "ModificationDate", "getModificationDate"):
        val = getattr(obj, attr, None)
        try:
            v = val() if callable(val) else val
            if not v:
                continue
            # Plone DateTime / datetime / str 都尽量处理
            if isinstance(v, DateTime):
                return v.asdatetime()
            return v
        except Exception:
            continue
    return None


def _uid_to_display(userid_or_uid, context):
    s = safe_unicode(userid_or_uid or "")
    # 识别 LabContact UID
    if s.startswith("labcontact-") and getToolByName:
        try:
            cat = getToolByName(context, "portal_catalog")
            brains = cat(UID=s)
            if brains:
                return safe_unicode(getattr(brains[0], "Title", "") or s)
        except Exception:
            pass
    # 否则按用户ID处理
    return _get_display_name_by_userid(s)


def compute_current_owner_for_ar(ar_obj):
    """
    返回 (stage_id, owner_display_name, task_count)

    规则：
    - 若命中状态强制负责人（如待审核、已发布），按覆盖表返回
    - 否则在“进行中的分析”里找检验员：以**最近修改**的分析来决定显示的用户名，并统计该检验员的任务数
    - 若 Analysis 层没找到，尝试 AR 层常见字段
    - 仍然找不到 → 回退 LabManager（仅显示阶段，不跟人）
    """

    ar_id = getattr(ar_obj, "getId", lambda: u"?")()
    state_id = _wf_state_id(ar_obj)  # 取样本状态 id
    # _log_info(u"[OWNER] >>> AR=%s, state=%s", ar_id, state_id)—

    if state_id in ("to_be_verified", "verified", "published") and _get_department_manager_userid:
        wf_tool = api.get_tool("portal_workflow")
        mgr_ids = set()

        for ana in _iter_analyses_for_ar(ar_obj):
            # 跳过隐藏的分析
            hidden = getattr(ana, "getHidden", lambda: None)()
            if hidden:
                continue

            try:
                st = wf_tool.getInfoFor(ana, "review_state", None)
            except Exception:
                st = _read_attr_value(ana, "review_state") or None
            st = (st or "").strip()
            if st != state_id:
                continue

            mgr_id = _get_department_manager_userid(ana)
            if mgr_id:
                mgr_ids.add(safe_unicode(mgr_id))

        if mgr_ids:
            # 如果同一个样本里有多个部门 Manager，就把名字拼在一起
            names = [
                _uid_to_display(uid, ar_obj)
                for uid in sorted(mgr_ids)
            ]
            disp = u"、".join(names)
            return state_id, disp, 0

    if state_id in STATE_OWNER_OVERRIDES:
        fixed_uid = STATE_OWNER_OVERRIDES[state_id]
        disp = _uid_to_display(fixed_uid, ar_obj)
        # _log_info(u"[OWNER] state override hit: %s -> %s", state_id, disp)
        return state_id, disp, 0

    # 分析层：进行中 + 有检验员（谁最近改动就显示谁）——
    active_rows = []  # [(modified_dt, uid)]
    uid_counter = defaultdict(int)
    total = with_analyst = active = 0

    try:
        for ana in _iter_analyses_for_ar(ar_obj):
            total += 1
            uid = _analysis_assignee(ana, ar_id)
            if uid:
                with_analyst += 1

            if not _is_analysis_in_progress(ana):
                continue

            active += 1
            if not uid:
                _log_debug(u"[OWNER] AR=%s analysis=%s in-progress but NO assignee",
                           ar_id, getattr(ana, "id", "?"))
                continue

            uid = safe_unicode(uid)
            uid_counter[uid] += 1
            ts = _modified_dt(ana)
            active_rows.append((ts, uid))
    except Exception as e:
        _log_exc(u"[OWNER] iterate analyses failed for AR=%s: %r", ar_id, e)

    # _log_info(u"[OWNER] AR=%s analyses: total=%s, with_assignee=%s, active=%s",
    #           ar_id, total, with_analyst, active)

    if active_rows:
        # 最近修改时间优先
        active_rows.sort(key=lambda x: (x[0] is None, x[0]), reverse=True)
        ts, current_uid = active_rows[0]
        owner_name = _uid_to_display(current_uid, ar_obj)
        task_count = uid_counter.get(current_uid, 1) or 1
        # _log_info(u"[OWNER] AR=%s choose latest in-progress: ts=%s uid=%s (%s task(s))",
        #           ar_id, ts, current_uid, task_count)
        return state_id, owner_name, task_count

    for attr in OWNER_PROBE_ATTRS:
        ar_uid = _read_attr_value(ar_obj, attr)
        if ar_uid:
            disp = _uid_to_display(ar_uid, ar_obj)
            # _log_info(u"[OWNER][AR-LEVEL] %s=%s -> %s", attr, ar_uid, disp)
            return state_id, disp, 0

    # _log_warn(u"[OWNER] AR=%s no assignee found on analyses/AR; fallback LabManager", ar_id)
    return state_id, _labmanager_display(ar_obj), 0


def render_owner_badge_for_item(real_obj):
    """把上面的计算结果渲染成列表上的 Owner 文本（含状态 badge）"""
    try:
        stage, owner_str, task_count = compute_current_owner_for_ar(real_obj)

        # 取 AR 的 UID
        try:
            uid = api.get_uid(real_obj) or u""
        except Exception:
            uid = u""

        # 生成可点击的 badge（带 uid 查询参数）
        badge = (
            u'<a class="badge badge-info pat-plone-modal" '
            u'data-pat-plone-modal="width: 800; height: 520; reloadWindowOnClose:false" '
            u'href="{url}/@@owner_history{qs}">'
            u'{stage}</a>'
        ).format(
            url=safe_unicode(real_obj.absolute_url()),
            qs=(u"?uid=" + safe_unicode(uid)) if uid else u"",
            stage=safe_unicode(stage or u""),
        )

        who = safe_unicode(owner_str) if owner_str else safe_unicode(_labmanager_display(real_obj))
        return u'<div class="owner-cell">{badge}<div class="owner-name">{who}</div></div>'.format(
            badge=badge, who=who)

    except Exception as e:
        _log_exc(u"[OWNER] render owner failed: %s", e)
        return u'<span class="badge badge-info">Owner</span>'


@implementer(ISamplesView)
class SamplesView(ListingView):
    """Listing View for Samples (AnalysisRequest content type) in the System
    """

    def __init__(self, context, request):
        super(SamplesView, self).__init__(context, request)

        self.mtool = getToolByName(context, "portal_membership")
        self.member = self.mtool.getAuthenticatedMember()
        self.roles = self.member.getRoles()

        logger.warn(">>> current user: %s, roles: %s",
                    self.member.getId(), self.roles)

        self.catalog = SAMPLE_CATALOG
        self.contentFilter = {
            "sort_on": "created",
            "sort_order": "descending",
            "isRootAncestor": True,
        }

        self.title = self.context.translate(_("Samples"))
        self.description = ""

        self.show_select_column = True
        self.form_id = "samples"
        self.context_actions = {}
        self.icon = "{}{}".format(
            self.portal_url, "/senaite_theme/icon/sample")

        self.url = api.get_url(self.context)

        # Toggle some columns if the sampling workflow is enabled
        sampling_enabled = api.get_setup().getSamplingWorkflowEnabled()

        now = DateTime().strftime("%Y-%m-%d %H:%M")

        self.columns = collections.OrderedDict((
            ("Priority", {
                "title": "",
                "index": "getPrioritySortkey",
                "sortable": True, }),
            ("Progress", {
                "title": _("Progress"),
                "index": "getProgress",
                "sortable": True,
                "toggle": False}),

            ("Owner", {
                "title": _("Progress"),
                "sortable": False,
                "toggle": True,
                "permission": ""
            }),

            ("ReportPDF", {
                "title": _(u"结果报告"),
                "sortable": False,
                "toggle": True,
            }),

            ("getId", {
                "title": _("Sample ID"),
                "attr": "getId",
                "replace_url": "getURL",
                "index": "getId"}),

            ("SubjectUID", {
                "title": _("label_subjectuid", default="Subject UID"),
                "sortable": True,
                "toggle": True,
                "index": "getSubjectUID"
            }),

            ("CancerType", {
                "title": _("label_cancertype", default="cancertype"),
                "sortable": False,
                "index": "getCancerType"
            }),

            ("ProjectName", {
                "title": _("Projects"),
                "sortable": False}),

            ("getSampleTypeTitle", {
                "title": _("Sample Type"),
                "sortable": True,
                "toggle": True}),

            ("Creator", {
                "title": _("Creator"),
                "index": "Creator",
                "sortable": True,
                "toggle": True}),

            ("getDateSampled", {
                "title": _("Date Sampled"),
                "toggle": True,
                "type": "datetime",
                "max": now,
                "sortable": True}),

            ("TrueDateReceived", {
                "title": _("label_true_date_received"),
                "toggle": True,
                "type": "datetime",
                "max": now}),

            ("getDateReceived", {
                "title": _("Date Received"),
                "toggle": True}),

            ("Client", {
                "title": _("Client"),
                "index": "getClientTitle",
                "attr": "getClientTitle",
                "replace_url": "getClientURL",
                "toggle": True}),

            ("ClientID", {
                "title": _("Client ID"),
                "index": "getClientID",
                "attr": "getClientID",
                "replace_url": "getClientURL",
                "toggle": True}),

            ("state_title", {
                "title": _("State"),
                "sortable": True,
                "index": "review_state"}),

            # ("NucleicAcidType", {
            #     "title": _("label_nucleicacidtype", default="nucleicacidtype"),
            #     "sortable": False,
            #     "index": "getNucleicAcidType"
            # }),
            #
            # ("TissueType", {
            #     "title": _("label_tissuetype", default="tissuetype"),
            #     "sortable": False,
            #     "index": "getTissueType"
            # }),

            ("Ethnicity", {
                "title": _("label_ethnicity", default="ethnicity"),
                "sortable": False,
                "index": "getEthnicity"
            }),

            ("SampleCode", {
                "title": _("label_samplecode", default="Sample Code"),
                "sortable": True,
                "toggle": True,
                "index": "getSampleCode"
            }),

            ("getClientOrderNumber", {
                "title": _("Client Order"),
                "sortable": True,
                "toggle": False}),

            ("Created", {
                "title": _("Date Registered"),
                "index": "created",
                "toggle": False}),

            ("SamplingDate", {
                "title": _("Expected Sampling Date"),
                "index": "getSamplingDate",
                "toggle": sampling_enabled}),

            ("getDatePreserved", {
                "title": _("Date Preserved"),
                "toggle": False,
                "type": "datetime",
                "max": now,
                "sortable": False}),  # no datesort without index

            ("getDueDate", {
                "title": _("Due Date"),
                "toggle": False}),
            ("getDateVerified", {
                "title": _("Date Verified"),
                "input_width": "10",
                "toggle": False}),
            ("getDatePublished", {
                "title": _("Date Published"),
                "toggle": False}),
            ("BatchID", {
                "title": _("Batch ID"),
                "index": "getBatchID",
                "sortable": True,
                "toggle": False}),
            ("Province", {
                "title": _("Province"),
                "sortable": True,
                "index": "getProvince",
                "attr": "getProvince",
                "toggle": False}),
            ("District", {
                "title": _("District"),
                "sortable": True,
                "index": "getDistrict",
                "attr": "getDistrict",
                "toggle": False}),
            ("getClientReference", {
                "title": _("Client Ref"),
                "sortable": True,
                "index": "getClientReference",
                "toggle": False}),
            ("getClientSampleID", {
                "title": _("Client SID"),
                "toggle": False}),
            ("ClientContact", {
                "title": _("Contact"),
                "sortable": False,
                "toggle": False}),
            ("getSamplePointTitle", {
                "title": _("Sample Point"),
                "sortable": True,
                "index": "getSamplePointTitle",
                "toggle": False}),
            ("getStorageLocation", {
                "title": _("Storage Location"),
                "sortable": True,
                "index": "getStorageLocationTitle",
                "toggle": False}),
            ("SamplingDeviation", {
                "title": _("Sampling Deviation"),
                "sortable": True,
                "index": "getSamplingDeviationTitle",
                "toggle": False}),
            ("getSampler", {
                "title": _("Sampler"),
                "toggle": sampling_enabled}),
            ("getPreserver", {
                "title": _("Preserver"),
                "sortable": False,
                "toggle": False}),
            ("getProfilesTitle", {
                "title": _("Profile"),
                "sortable": False,
                "toggle": False}),
            ("getAnalysesNum", {
                "title": _("Number of Analyses"),
                "alt": _("Open / To be verified / Verified / Total"),
                "sortable": True,
                "index": "getAnalysesNum",
                "toggle": False}),
            ("getTemplateTitle", {
                "title": _("Template"),
                "sortable": True,
                "index": "getTemplateTitle",
                "toggle": False}),
            ("Printed", {
                "title": _("Printed"),
                "sortable": False,
                "index": "getPrinted",
                "toggle": False}),

            ("Diagnosis", {
                "title": _("label_diagnosis", default="Diagnosis (Primary/Metastatic)"),
                "sortable": True,
                "toggle": False,
                "index": "getDiagnosis"
            }),

            ("SampleQuantity", {
                "title": _("label_samplequantity", default="Sample Quantity"),
                "sortable": True,
                "index": "getSampleQuantity",
                "toggle": False
            }),

            ("DeliveryDate", {
                "title": _("label_deliverydate", default="Delivery Date"),
                "sortable": True,
                "index": "getDeliveryDate",
                "toggle": False
            }),

            # ("SampleClassification", {
            #     "title": _("label_sampleclassification", default="sampleclassification"),
            #     "sortable": False,
            #     "index": "getSampleClassification"
            # }),
            #
            # ("SampleSource", {
            #     "title": _("label_samplesource", default="samplesource"),
            #     "sortable": False,
            #     "index": "getSampleSource"
            # }),
        ))

        # custom print transition
        print_stickers = {
            "id": "print_stickers",
            "title": _("Print stickers"),
            "url": "{}/workflow_action?action=print_stickers".format(self.url)
        }

        self.review_states = [
            {
                "id": "default",
                "title": _("Active"),
                "contentFilter": {
                    "review_state": (
                        "sample_received",
                    ),
                    "sort_on": "created",
                    "sort_order": "descending",
                },
                "custom_transitions": [print_stickers],
                "columns": self.columns.keys(),
            },
            {
                "id": "to_be_sampled",
                "title": _("To Be Sampled"),
                "contentFilter": {
                    "review_state": ("to_be_sampled",),
                    "sort_on": "created",
                    "sort_order": "descending"},
                "custom_transitions": [print_stickers],
                "columns": self.columns.keys()
            },
            {
                "id": "to_be_preserved",
                "title": _("To Be Preserved"),
                "contentFilter": {
                    "review_state": ("to_be_preserved",),
                    "sort_on": "created",
                    "sort_order": "descending",
                },
                "custom_transitions": [print_stickers],
                "columns": self.columns.keys(),
            }, {
                "id": "scheduled_sampling",
                "title": _("Scheduled sampling"),
                "contentFilter": {
                    "review_state": ("scheduled_sampling",),
                    "sort_on": "created",
                    "sort_order": "descending",
                },
                "custom_transitions": [print_stickers],
                "columns": self.columns.keys(),
            }, {
                "id": "sample_due",
                "title": _("Due"),
                "contentFilter": {
                    "review_state": (
                        "to_be_sampled",
                        "to_be_preserved",
                        "sample_due"),
                    "sort_on": "created",
                    "sort_order": "descending"},
                "custom_transitions": [print_stickers],
                "columns": self.columns.keys(),
            }, {
                "id": "sample_received",
                "title": _("Received"),
                "contentFilter": {
                    "review_state": "sample_received",
                    "sort_on": "created",
                    "sort_order": "descending",
                },
                "custom_transitions": [print_stickers],
                "columns": self.columns.keys(),
            }, {
                "id": "to_be_verified",
                "title": _("To be verified"),
                "contentFilter": {
                    "review_state": "to_be_verified",
                    "sort_on": "created",
                    "sort_order": "descending",
                },
                "custom_transitions": [print_stickers],
                "columns": self.columns.keys(),
            }, {
                "id": "verified",
                "title": _("Verified"),
                "contentFilter": {
                    "review_state": "verified",
                    "sort_on": "created",
                    "sort_order": "descending",
                },
                "custom_transitions": [print_stickers],
                "columns": self.columns.keys(),
            }, {
                "id": "published",
                "title": _("Published"),
                "contentFilter": {
                    "review_state": ("published"),
                    "sort_on": "created",
                    "sort_order": "descending",
                },
                "custom_transitions": [],
                "columns": self.columns.keys(),
            }, {
                "id": "dispatched",
                "title": _("Dispatched"),
                "flat_listing": True,
                "confirm_transitions": ["restore"],
                "contentFilter": {
                    "review_state": ("dispatched"),
                    "sort_on": "created",
                    "sort_order": "descending",
                },
                "custom_transitions": [],
                "columns": self.columns.keys(),
            }, {
                "id": "cancelled",
                "title": _("Cancelled"),
                "contentFilter": {
                    "review_state": "cancelled",
                    "sort_on": "created",
                    "sort_order": "descending",
                },
                "custom_transitions": [],
                "columns": self.columns.keys(),
            }, {
                "id": "invalid",
                "title": _("Invalid"),
                "contentFilter": {
                    "review_state": "invalid",
                    "sort_on": "created",
                    "sort_order": "descending",
                },
                "custom_transitions": [print_stickers],
                "columns": self.columns.keys(),
            }, {
                "id": "all",
                "title": _("All"),
                "contentFilter": {
                    "sort_on": "created",
                    "sort_order": "descending",
                    "review_state": [
                        "to_be_sampled",
                        "sample_due",
                        "sample_received",
                        "to_be_verified",
                        "verified",
                        "published",
                        "stored",
                    ],
                },
                "custom_transitions": [print_stickers],
                "columns": self.columns.keys(),
            }, {
                "id": "rejected",
                "title": _("Rejected"),
                "contentFilter": {
                    "review_state": "rejected",
                    "sort_on": "created",
                    "sort_order": "descending",
                },
                "custom_transitions": [print_stickers],
                "columns": self.columns.keys(),
            }, {
                "id": "assigned",
                "title": get_image("assigned.png",
                                   title=t(_("Assigned"))),
                "contentFilter": {
                    "assigned_state": "assigned",
                    "review_state": ("sample_received",),
                    "sort_on": "created",
                    "sort_order": "descending",
                },
                "custom_transitions": [print_stickers],
                "columns": self.columns.keys(),
            }, {
                "id": "unassigned",
                "title": get_image("unassigned.png",
                                   title=t(_("Unsassigned"))),
                "contentFilter": {
                    "assigned_state": "unassigned",
                    "review_state": (
                        "sample_received",
                    ),
                    "sort_on": "created",
                    "sort_order": "descending",
                },
                "custom_transitions": [print_stickers],
                "columns": self.columns.keys(),
            }, {
                "id": "late",
                "title": get_image("late.png",
                                   title=t(_("Late"))),
                "contentFilter": {
                    # Query only for unpublished ARs that are late
                    "review_state": (
                        "sample_received",
                        "to_be_verified",
                        "verified",
                    ),

                    "getDueDate": {
                        "query": DateTime(),
                        "range": "max",
                    },
                    "sort_on": "created",
                    "sort_order": "descending",
                },
                "custom_transitions": [print_stickers],
                "columns": self.columns.keys(),
            }
        ]


    def _is_effective(self):
        # 优先用 update/before_render 里已经算好的 roles
        roles = set(getattr(self, "roles", []) or [])

        # 若没有，则从当前用户取（兼容不同实现）
        if not roles:
            try:
                user = api.get_current_user()  # 相当于 mtool.getAuthenticatedMember()
                if hasattr(user, "getRolesInContext"):
                    roles = set(user.getRolesInContext(self.context))
                else:
                    roles = set(user.getRoles())
            except Exception:
                roles = set()

        effective = "Authenticated" in roles and "Manager" not in roles  # 排除匿名用户，其他用户都走分配过滤
        return effective

    def _assigned_ar_uids_for_me(self):
        """以对象真实状态为准，返回样本(AR) UID 集合"""
        user = api.get_current_user()
        userid = user.getId()
        uids = set()

        ac = api.get_tool("senaite_catalog_analysis") or api.get_tool("bika_analysis_catalog")
        if not ac:
            # _log_warn("[SamplesView] analysis catalog not found")
            return uids

        # 先用 catalog 粗筛“跟我有关”的分析（索引可能滞后，所以下面还要对象级复核）
        candidate_brains = []
        for q in ({"getAnalyst": userid}, {"Analyst": userid}, {"getResponsible": userid}):
            try:
                res = ac.searchResults(q)
                if res:
                    candidate_brains.extend(res)
                    _log_debug(u"[SamplesView] analysis catalog hit with %d result(s) for query=%s", len(res), q)
            except Exception as e:
                _log_debug(u"[SamplesView] analysis catalog query failed for %s: %r", q, e)

        # 仍需处理的状态（撤回后通常变为 unassigned，不在此集合内4）
        ACTIVE_STATES = {"assigned"}

        # 若站点启用了 AllowedServices，这里叠加白名单
        allowed = None
        try:
            if hasattr(self, "get_allowed_service_keywords_for_user"):
                allowed = set(self.get_allowed_service_keywords_for_user(userid) or [])
        except Exception:
            allowed = None

        dropped_stale = dropped_state = dropped_hidden = dropped_service = 0

        # 对象级复核，避免 catalog 索引延迟造成“撤回了还看得到”
        pwf = api.get_tool("portal_workflow")
        for b in candidate_brains:
            try:
                analysis = api.get_object(b)
            except Exception:
                continue

            # 仍分派给我（对象字段，而不是 brain）
            analyst = getattr(analysis, "getAnalyst", lambda: None)()
            if analyst != userid:
                dropped_stale += 1
                continue

            # 状态严格为 assigned（对象当前状态）
            try:
                state = pwf.getInfoFor(analysis, "review_state", None)
            except Exception:
                state = getattr(b, "review_state", None)
            if state not in ACTIVE_STATES:
                dropped_state += 1
                continue

            # 未隐藏
            hidden = getattr(analysis, "getHidden", lambda: None)()
            if hidden is True:
                dropped_hidden += 1
                continue

            # 服务权限
            if allowed:
                kw = None
                try:
                    service = getattr(analysis, "getService", lambda: None)()
                    if service:
                        kw = getattr(service, "getKeyword", lambda: None)()
                except Exception:
                    pass
                if not kw:
                    kw = (
                            getattr(b, "getServiceKeyword", None)
                            or getattr(b, "ServiceKeyword", None)
                            or getattr(b, "keyword", None)
                    )
                if kw and kw not in allowed:
                    dropped_service += 1
                    continue

            # 映射到样本 UID（从对象拿最稳妥）
            ar = getattr(analysis, "getRequest", lambda: None)() or analysis.aq_parent
            if ar:
                uids.add(api.get_uid(ar))

        return uids

    def _manager_ar_uids_for_state(self, target_state):
        """当前登录 LabManager 在某个状态下应看到的样本 UID 集合。

        target_state: "to_be_verified" / "verified" / "published" 等

        条件（针对某个样本 AR）：
          1至少有一条该经理负责的分析，状态是 target_state；
          2该样本自身的 review_state 也等于 target_state。
        """

        user = api.get_current_user()
        userid = user.getId()
        wf_tool = api.get_tool("portal_workflow")

        ac = api.get_tool("senaite_catalog_analysis") or \
             api.get_tool("bika_analysis_catalog")
        if not ac:
            _log_warn(u"[SamplesView][manager] analysis catalog not found")
            return set()

        try:
            brains = ac.searchResults(review_state=target_state)
        except Exception as e:
            _log_debug(
                u"[SamplesView] analysis catalog query(%s) failed: %r",
                target_state, e,
            )
            return set()

        # # 有多少条该状态的分析
        # _log_info(
        #     u"[SamplesView][manager] user=%s target_state=%s brains=%s",
        #     userid, target_state, len(brains),
        # )
        try:
            from bika.lims.workflow.analysis.test import (
                _get_department_manager_userid,
            )
        except Exception as e:
            return set()

        candidate_uids = set()

        # candidate_uids，有至少一条本经理负责且处于 target_state 的分析
        for b in brains:
            try:
                analysis = api.get_object(b)
            except Exception as e:
                _log_debug(
                    u"[SamplesView][manager] get_object for brain %r failed: %r",
                    getattr(b, "getPath", lambda: u"?")(), e,
                )
                continue

            try:
                state = wf_tool.getInfoFor(analysis, "review_state", None)
            except Exception:
                state = getattr(b, "review_state", None)

            if state != target_state:
                _log_debug(
                    u"[SamplesView][manager] skip ana=%s: state=%s != target_state=%s",
                    getattr(analysis, "id", u"?"), state, target_state,
                )
                continue

            hidden = getattr(analysis, "getHidden", lambda: None)()
            if hidden:
                _log_debug(
                    u"[SamplesView][manager] skip ana=%s: hidden=%r",
                    getattr(analysis, "id", u"?"), hidden,
                )
                continue

            mgr_id = _get_department_manager_userid(analysis)

            # 打印每条分析的部门经理
            _log_debug(
                u"[SamplesView][manager] ana=%s state=%s mgr_id=%s (current=%s)",
                getattr(analysis, "id", u"?"), state, mgr_id, userid,
            )

            if not mgr_id or mgr_id != userid:
                # 不是当前经理负责
                continue

            ar = getattr(analysis, "getRequest", lambda: None)() or \
                 getattr(analysis, "aq_parent", None)
            if not ar:
                _log_debug(
                    u"[SamplesView][manager] ana=%s has no AR",
                    getattr(analysis, "id", u"?"),
                )
                continue

            try:
                uid = api.get_uid(ar)
            except Exception as e:
                _log_debug(
                    u"[SamplesView][manager] get_uid for AR of ana=%s failed: %r",
                    getattr(analysis, "id", u"?"), e,
                )
                uid = None

            if uid:
                candidate_uids.add(uid)
                _log_debug(
                    u"[SamplesView][manager] add candidate AR uid=%s (from ana=%s)",
                    uid, getattr(analysis, "id", u"?"),
                )

        final_uids = set()

        # ----再根据样本自身状态过滤 ----
        for uid in candidate_uids:
            ar = api.get_object_by_uid(uid, None)
            if ar is None:
                _log_debug(
                    u"[SamplesView][manager] AR uid=%s not found by api.get_object_by_uid",
                    uid,
                )
                continue

            try:
                ar_state = wf_tool.getInfoFor(ar, "review_state", None)
            except Exception:
                ar_state = getattr(ar, "review_state", None)

            _log_debug(
                u"[SamplesView][manager] check AR=%s uid=%s ar_state=%s target_state=%s",
                getattr(ar, "getId", lambda: u"?")(),
                uid, ar_state, target_state,
            )

            if ar_state != target_state:
                _log_debug(
                    u"[SamplesView][manager] skip AR=%s uid=%s: ar_state=%s != %s",
                    getattr(ar, "getId", lambda: u"?")(),
                    uid, ar_state, target_state,
                )
                continue

            final_uids.add(uid)

        # 最终结果
        # _log_info(
        #     u"[SamplesView][manager] user=%s target_state=%s FINAL count=%s uids=%s",
        #     userid, target_state, len(final_uids), list(final_uids),
        # )

        return final_uids

    def update(self):
        """Called before the listing renders"""
        super(SamplesView, self).update()

        # --- 把 GET 里的样本类型参数保存到 form，保证后续 ajax_folderitems 能拿到 ---
        req = self.request
        st = (req.get('getSampleTypeUID') or
              req.get('samples_getSampleTypeUID') or
              (getattr(req, "form", {}) or {}).get('getSampleTypeUID') or
              (getattr(req, "form", {}) or {}).get('samples_getSampleTypeUID'))

        if st:
            # 规范化单值
            if isinstance(st, (list, tuple)):
                st = next((x for x in st if x), None)
            # 同时写入两种键名：裸键 + 前缀键（ListingView 习惯用前缀）
            req.form['getSampleTypeUID'] = st
            req.form['samples_getSampleTypeUID'] = st

        self.workflow = api.get_tool("portal_workflow")
        self.member = self.mtool.getAuthenticatedMember()
        self.roles = self.member.getRoles()
        roles = set(self.roles or [])

        self.allowed_roles = ["LabManager", "Manager"]
        self.is_privileged = any(role in self.roles for role in self.allowed_roles)

        self.white_list = [
            "Priority", "Progress", "getId", "Creator", "SamplingDate", "SubjectUID",
            "getDateSampled", "Client", "ClientID", "getSampleTypeTitle", "Province",
            "getTrueDateReceived", "getDateReceived", "Owner"
        ]

        if not self.is_privileged:
            for col in list(self.columns.keys()):
                if col not in self.white_list:
                    del self.columns[col]

        # —— 先按原有角色设默认页签/可见页签 ——
        if "Publisher" in roles:
            self.default_review_state = "to_be_verified"

        elif ({"LabClerk", "Sampler"} & roles) and ("Analyst" not in roles):
            self.default_review_state = "sample_due"
            allowed = {"sample_due", "sample_received"}
            required = {"default", "published"}
            safe_allowed = allowed | required
            self.review_states = [rv for rv in self.review_states if rv["id"] in safe_allowed]

        elif "LabManager" in roles:
            self.default_review_state = "sample_received"

        else:
            self.default_review_state = "default"

        # —— 最终覆盖：只要含 Analyst，就默认“正在进行中”
        if "Analyst" in roles:
            self.default_review_state = "default"
            # 若同时含 LabClerk/Sampler，仅保留四个标签
            if roles & {"LabClerk", "Sampler"}:
                keep = {"default", "sample_due", "sample_received", "published"}
                self.review_states = [rv for rv in self.review_states if rv.get("id") in keep]

        if not (set(self.roles or []) & {"Manager", "LabManager"}):
            for k in ("Owner", "ReportPDF"):
                self.columns.pop(k, None)
            for rv in self.review_states:
                rv["columns"] = [c for c in rv.get("columns", []) if c not in ("Owner", "ReportPDF")]

        self.purge_review_states()
        self.purge_columns()
        self.add_custom_transitions()

    def get_catalog_query(self, *args, **kwargs):
        query = super(SamplesView, self).get_catalog_query(*args, **kwargs)

        req = self.request
        form = getattr(req, "form", {}) or {}

        def _first_val(v):
            if isinstance(v, (list, tuple)):
                return next((x for x in v if x), None)
            return v

        # 1) 先从 form 里拿（ajax 提交常在 form）
        st = form.get('samples_getSampleTypeUID') or form.get('getSampleTypeUID')
        st = _first_val(st)

        # 2) 再从 request dict 拿（GET/POST 都可能在这里）
        if not st:
            st = req.get('getSampleTypeUID') or req.get('samples_getSampleTypeUID')
            st = _first_val(st)

        # 3) 最后直接解析 QueryString
        if not st:
            try:
                qs = req.get('QUERY_STRING', '') or ''
                if 'getSampleTypeUID=' in qs:
                    try:
                        from urlparse import parse_qs  # Py2
                    except ImportError:
                        from urllib.parse import parse_qs  # Py3
                    st = _first_val(parse_qs(qs).get('getSampleTypeUID'))
            except Exception:
                st = None

        # 到 catalog 查询
        if st:
            query['getSampleTypeUID'] = st

        return query

    def before_render(self):
        """Before template render hook
        """

        super(SamplesView, self).before_render()
        # remove query filter for root samples when listing is flat
        if self.flat_listing:
            self.contentFilter.pop("isRootAncestor", None)

    def _get_total_analyses(self, brain_or_obj):
        """getAnalysesNum -> [verified, total, not_submitted, to_be_verified]"""
        ana = getattr(brain_or_obj, "getAnalysesNum", None)
        if ana is None:
            return 0
        try:
            if callable(ana):
                ana = ana()
        except Exception:
            pass

        try:
            if isinstance(ana, (list, tuple)) and len(ana) >= 2:
                return int(ana[1] or 0)
            return int(ana or 0)
        except Exception:
            return 0

    def _received_zero_project_uids(self):
        catalog = api.get_tool(SAMPLE_CATALOG)
        query = self.get_catalog_query()
        query.pop("sort_limit", None)
        brains = catalog(query)
        uids = []
        for b in brains:
            if self._get_total_analyses(b) == 0:
                uid = getattr(b, "UID", None)
                if callable(uid):
                    uid = uid()
                if uid:
                    uids.append(uid)
        return uids

    def folderitems(self):

        self.before_render()

        cur_id = (self.review_state or {}).get("id")
        roles = set(self.roles or {})

        allowed_uids = None

        if cur_id == "default" and self._is_effective():
            allowed_uids = self._assigned_ar_uids_for_me()

        elif cur_id in ("to_be_verified", "verified") and "LabManager" in roles:
            # 待复核 / 已复核：实验室管理员只看到自己负责的样本
            allowed_uids = self._manager_ar_uids_for_state(cur_id)

        if allowed_uids is not None:
            self.contentFilter["UID"] = (
                list(allowed_uids) if allowed_uids else ["__no_such_uid__"]
            )

        if cur_id == "sample_received":
            zero_uids = self._received_zero_project_uids()
            if not zero_uids:
                self.contentFilter["UID"] = ["__no_such_uid__"]
            else:
                # 如果上面已经有 UID 限制（比如 manager），这里做交集
                existing = self.contentFilter.get("UID")
                if existing and isinstance(existing, (list, tuple, set)):
                    existing_set = set(existing)
                    zero_uids = [u for u in zero_uids if u in existing_set]
                self.contentFilter["UID"] = zero_uids

        return super(SamplesView, self).folderitems()

    def folderitem(self, obj, item, index):
        # Additional info from AnalysisRequest to be added in the item
        # generated by default by bikalisting.
        # Call the folderitem method from the base class
        item = super(SamplesView, self).folderitem(obj, item, index)
        if not item:
            return None

        item["Creator"] = self.user_fullname(obj.Creator)
        item.setdefault("replace", {})
        project = api.safe_unicode(self.request.get("project", "") or "")
        client_uid = api.safe_unicode(self.request.get("client_uid", "") or "")
        client_id = api.safe_unicode(self.request.get("client_id", "") or "")

        creator_text = api.safe_unicode(item.get("Creator", "") or "")
        creator_value = api.safe_unicode(getattr(obj, "Creator", "") or "")
        if callable(getattr(obj, "Creator", None)):
            creator_value = api.safe_unicode(obj.Creator() or "")

        if creator_text and creator_value:
            item["replace"]["Creator"] = get_samples_filter_link(
                text=creator_text,
                context=self.context,
                filter_kind="creator",
                filter_value=creator_value,
                filter_label=creator_text,
                project=project,
                client_uid=client_uid,
                client_id=client_id,
            )

        # If we redirect from the folderitems view we should check if the
        # user has permissions to medify the element or not.
        priority_sort_key = obj.getPrioritySortkey
        if not priority_sort_key:
            # Default priority is Medium = 3.
            # The format of PrioritySortKey is <priority>.<created>
            priority_sort_key = "3.%s" % obj.created.ISO8601()
        priority = priority_sort_key.split(".")[0]
        priority_text = PRIORITIES.getValue(priority)
        priority_div = """<div class="priority-ico priority-%s">
                          <span class="notext">%s</span><div>
                       """
        item["replace"]["Priority"] = priority_div % (priority, priority_text)
        item["replace"]["getProfilesTitle"] = obj.getProfilesTitleStr
        # returns a list of
        # [verified, total, not_submitted, to_be_verified]
        analysesnum = obj.getAnalysesNum
        if analysesnum:
            numbers = {
                "verified": analysesnum[0],
                "verified_title": t(_("Verified")),
                "total": analysesnum[1],
                "total_title": t(_("Total")),
                "not_submitted": analysesnum[2],
                "not_submitted_title": t(_("Open")),
                "to_be_verified": analysesnum[3],
                "to_be_verified_title": t(_("To be verified")),
            }

            item["getAnalysesNum"] = ANALYSES_NUM_TPL.safe_substitute(numbers)
            html = ANALYSES_NUM_TPL_HTML.safe_substitute(numbers)
            item["replace"]["getAnalysesNum"] = html
        else:
            item["getAnalysesNum"] = ""

        # Progress
        progress_perc = obj.getProgress  # 直接取出百分比
        item["Progress"] = progress_perc
        item["replace"]["Progress"] = get_progress_bar_html(progress_perc)  # 渲染为进度条

        item["BatchID"] = obj.getBatchID
        if obj.getBatchID:
            item['replace']['BatchID'] = "<a href='%s'>%s</a>" % \
                                         (obj.getBatchURL, obj.getBatchID)
        # TODO: SubGroup ???
        # val = obj.Schema().getField('SubGroup').get(obj)
        # item['SubGroup'] = val.Title() if val else ''

        item["SamplingDate"] = self.str_date(obj.getSamplingDate)
        item["getDateSampled"] = self.str_date(obj.getDateSampled)
        item["getDateReceived"] = self.str_date(obj.getDateReceived)
        item["getDueDate"] = self.str_date(obj.getDueDate)
        item["getDatePublished"] = self.str_date(obj.getDatePublished)
        item["getDateVerified"] = self.str_date(obj.getDateVerified)

        if self.is_printing_workflow_enabled:
            item["Printed"] = ""
            printed = obj.getPrinted if hasattr(obj, "getPrinted") else "0"
            print_icon = ""
            if printed == "0":
                print_icon = get_image("delete.png",
                                       title=t(_("Not printed yet")))
            elif printed == "1":
                print_icon = get_image("ok.png",
                                       title=t(_("Printed")))
            elif printed == "2":
                print_icon = get_image(
                    "exclamation.png",
                    title=t(_("Republished after last print")))
            item["after"]["Printed"] = print_icon
        item["SamplingDeviation"] = obj.getSamplingDeviationTitle
        item["getStorageLocation"] = obj.getStorageLocationTitle

        after_icons = ""
        if obj.assigned_state == 'assigned':
            after_icons += get_image("worksheet.png",
                                     title=t(_("All analyses assigned")))
        if item["review_state"] == 'invalid':
            after_icons += get_image("delete.png",
                                     title=t(_("Results have been withdrawn")))

        due_date = obj.getDueDate
        if due_date and due_date < (obj.getDatePublished or DateTime()):
            due_date_str = self.ulocalized_time(due_date)
            # img_title = "{}: {}".format(t(_("Late Analyses")), due_date_str)
            img_title = u"{}: {}".format(api.safe_unicode(t(_("Late Analyses"))), due_date_str)
            after_icons += get_image("late.png", title=img_title)

        if obj.getSamplingDate and obj.getSamplingDate > DateTime():
            after_icons += get_image("calendar.png",
                                     title=t(_("Future dated sample")))
        if obj.getInvoiceExclude:
            after_icons += get_image("invoice_exclude.png",
                                     title=t(_("Exclude from invoice")))
        if obj.getHazardous:
            after_icons += get_image("hazardous.png",
                                     title=t(_("Hazardous")))

        if obj.getInternalUse:
            after_icons += get_image("locked.png", title=t(_("Internal use")))

        if after_icons:
            item['after']['getId'] = after_icons

        item['Created'] = self.ulocalized_time(obj.created, long_format=1)
        contact = self.get_object_by_uid(obj.getContactUID)
        if contact:
            item['ClientContact'] = contact.getFullname()
            item['replace']['ClientContact'] = get_link_for(contact)
        else:
            item["ClientContact"] = ""
        # TODO-performance: If SamplingWorkflowEnabled, we have to get the
        # full object to check the user permissions, so far this is
        # a performance hit.
        if obj.getSamplingWorkflowEnabled:

            sampler = obj.getSampler
            if sampler:
                item["getSampler"] = sampler
                item["replace"]["getSampler"] = self.user_fullname(sampler)

            # sampling workflow - inline edits for Sampler and Date Sampled
            if item["review_state"] == "to_be_sampled":
                # We need to get the full object in order to check
                # the permissions
                full_object = api.get_object(obj)
                if check_permission(TransitionSampleSample, full_object):
                    # make fields required and editable
                    item["required"] = ["getSampler", "getDateSampled"]
                    item["allow_edit"] = ["getSampler", "getDateSampled"]
                    date = obj.getDateSampled or DateTime()
                    # provide date and time in a valid input format
                    item["getDateSampled"] = self.to_datetime_input_value(date)
                    sampler_roles = ["Sampler", "LabManager", ""]
                    samplers = getUsers(full_object, sampler_roles)
                    users = [({
                        "ResultValue": u,
                        "ResultText": samplers.getValue(u)}) for u in samplers]
                    item["choices"] = {"getSampler": users}
                    # preselect the current user as sampler
                    if not sampler and "Sampler" in self.roles:
                        sampler = self.member.getUserName()
                        item["getSampler"] = sampler

        # These don't exist on ARs
        # XXX This should be a list of preservers...
        item["getPreserver"] = ""
        item["getDatePreserved"] = ""

        # Assign parent and children partitions of this sample
        if self.show_partitions:
            item["parent"] = obj.getRawParentAnalysisRequest
            item["children"] = obj.getDescendantsUIDs or []

        real_obj = obj.getObject()
        if not IAnalysisRequest.providedBy(real_obj):
            # _log_warn("Object is not AnalysisRequest, got: %s", obj.portal_type)
            return item

        # 统一从real_obj获取字段值，确保返回的是unicode字符串
        item["SampleCode"] = unicode(getattr(real_obj, "getSampleCode", lambda: "")(), 'utf-8')
        # item["SubjectUID"] = unicode(getattr(real_obj, "getSubjectUID", lambda: "")(), 'utf-8')
        subject_uid = getattr(real_obj, "getSubjectUID", lambda: "")()
        subject_uid = safe_unicode(subject_uid).strip()
        if subject_uid:
            item.setdefault("replace", {})
            item["replace"]["SubjectUID"] = get_subject_link(self.context, subject_uid, value=subject_uid)

            item["SubjectUID"] = subject_uid

        item["NameAbbreviation"] = unicode(getattr(real_obj, "getNameAbbreviation", lambda: "")(), 'utf-8')
        item['TrueDateReceived'] = self.str_date(getattr(real_obj, "getTrueDateReceived", lambda: None)())

        item["InitialScreeningDate"] = self.str_date(
            getattr(real_obj, "getInitialScreeningDate", lambda: None)()
        )
        item["AdmissionDate"] = self.str_date(
            getattr(real_obj, "getAdmissionDate", lambda: None)()
        )
        item["InfectionReportDate"] = self.str_date(
            getattr(real_obj, "getInfectionReportDate", lambda: None)()
        )
        item["SamplingDate"] = self.str_date(
            getattr(real_obj, "getSamplingDate", lambda: None)()
        )
        item["DeliveryDate"] = self.str_date(
            getattr(real_obj, "getDeliveryDate", lambda: None)()
        )
        item["TissueType"] = unicode(getattr(real_obj, "getTissueType", lambda: "")(), 'utf-8')
        # item["CancerType"] = unicode(getattr(real_obj, "getCancerType", lambda: "")(), 'utf-8')
        cancer_text = unicode(getattr(real_obj, "getCancerType", lambda: "")(), 'utf-8')
        item['CancerType'] = cancer_text

        if cancer_text:
            item.setdefault("replace", {})
            item["replace"]["CancerType"] = get_samples_filter_link(
                text=cancer_text,
                context=self.context,
                filter_kind="cancer",
                filter_value=cancer_text,
                filter_label=cancer_text,
                project=project,
                client_uid=client_uid,
                client_id=client_id,
            )

        st_uid = u""
        st_title = u""
        try:
            st_uid = getattr(real_obj, "getSampleTypeUID", lambda: u"")()
            st_uid = safe_unicode(st_uid).strip()
        except Exception:
            st_uid = u""

        try:
            st_title = getattr(real_obj, "getSampleTypeTitle", lambda: u"")()
            st_title = safe_unicode(st_title).strip()
        except Exception:
            st_title = u""

        if not st_title:
            try:
                st_obj = getattr(real_obj, "getSampleType", lambda: None)()
                if st_obj:
                    title_fn = getattr(st_obj, "Title", None)  # 注意：不要叫 t
                    st_title = safe_unicode(title_fn() if callable(title_fn) else title_fn).strip()
            except Exception:
                st_title = u""

        item["getSampleTypeTitle"] = st_title
        item["getSampleTypeUID"] = st_uid

        if st_uid and st_title:
            item.setdefault("replace", {})
            item["replace"]["getSampleTypeTitle"] = get_samples_filter_link(
                text=st_title,
                context=self.context,
                filter_kind="sample_type",
                filter_value=st_uid,  # 过滤用 UID（关键）
                filter_label=st_title,  # 页面显示标题
                project=project,
                client_uid=client_uid,
                client_id=client_id,
            )

        project = getattr(real_obj, "getProjectName", lambda: "")()
        project = safe_unicode(project).strip()
        item["ProjectName"] = project
        if project:
            item.setdefault("replace", {})
            item["replace"]["ProjectName"] = get_project_link(
                self.context,
                project,
                title=project,
                review_state="all",
                # 如果想同时带中心过滤：
                # extra={"client_uid": item.get("ClientUID"), "client_id": item.get("ClientID")},
                csrf=False
            )

        _eth = getattr(real_obj, "getEthnicity", lambda: u"")()
        _eth = safe_unicode(_eth)

        if _eth and re.search(u"[\u4e00-\u9fff]", _eth):
            # 本来就是中文，比如“日本”“其他国家”
            item["Ethnicity"] = _eth
        else:
            # 英文的，再走翻译：Chinese -> 中国，Foreign -> 其他国家
            item["Ethnicity"] = t(_(_eth)) if _eth else u""

        item["SampleClassification"] = unicode(getattr(real_obj, "getSampleClassification", lambda: "")(), 'utf-8')
        item["SampleSource"] = unicode(getattr(real_obj, "getSampleSource", lambda: "")(), 'utf-8')
        item["library"] = unicode(getattr(real_obj, "getLibrary", lambda: "")(), 'utf-8')
        item["LibrarySubmissionTime"] = unicode(getattr(real_obj, "getLibrarySubmissionTime", lambda: "")(), 'utf-8')
        item["SampleName"] = unicode(getattr(real_obj, "getSampleName", lambda: "")(), 'utf-8')

        # —— 这里渲染 Owner（带全量日志）
        item["Owner"] = render_owner_badge_for_item(real_obj)

        # —— 报告 PDF ——（folderitem 内放在合适位置）
        COLUMN_KEY = "ReportPDF"

        ar_id_str = safe_unicode(getattr(real_obj, "getId", lambda: "?")())

        try:
            # 只传 ar 实例，不要额外传 self
            report_url = self._resolve_latest_report_pdf(real_obj)
        except Exception as e:
            _log_exc(u"[Report] folderitem resolve failed: AR=%s err=%r", ar_id_str, e)
            report_url = u""

        item.setdefault("replace", {})

        if report_url:
            item[COLUMN_KEY] = u"结果报告"
            item["replace"][COLUMN_KEY] = (
                u'<a href="{url}" target="_blank" class="badge badge-light" title="PDF">下载结果报告</a>'
            ).format(url=safe_unicode(report_url))
        else:
            item[COLUMN_KEY] = u""

        return item

    @view.memoize
    def get_object_by_uid(self, uid):
        """Returns the object for the given uid
        """
        return api.get_object_by_uid(uid, default=None)

    def purge_review_states(self):

        """ Purges unnecessary review statuses
        """
        remove_filters = []
        setup = api.get_bika_setup()
        if not setup.getSamplingWorkflowEnabled():
            remove_filters.append("to_be_sampled")
        if not setup.getScheduleSamplingEnabled():
            remove_filters.append("scheduled_sampling")
        if not setup.getSamplePreservationEnabled():
            remove_filters.append("to_be_preserved")
        if not setup.getRejectionReasons():
            remove_filters.append("rejected")

        self.review_states = filter(lambda r: r.get("id") not in remove_filters,
                                    self.review_states)

    def purge_columns(self):
        """Purges unnecessary columns
        """
        remove_columns = []
        if not self.is_printing_workflow_enabled:
            remove_columns.append("Printed")

        for rv in self.review_states:
            cols = rv.get("columns", [])
            rv["columns"] = filter(lambda c: c not in remove_columns, cols)

    def add_custom_transitions(self):

        custom_transitions = []
        if self.is_printing_workflow_enabled:
            custom_transitions.append({
                "id": "print_sample",
                "title": _("Print"),
                "url": "{}/workflow_action?action={}".format(
                    self.url, "print_sample")
            })

        copy_to_new = self.get_copy_to_new_transition()
        if copy_to_new:
            custom_transitions.append(copy_to_new)

        # Allow to create a worksheet for the selected samples
        if self.can_create_worksheet():
            custom_transitions.append({
                "id": "modal_create_worksheet",
                "title": _("Create Worksheet"),
                "url": "{}/create_worksheet_modal".format(
                    api.get_url(self.context)),
                "css_class": "btn btn-outline-secondary",
                "help": _("Create a new worksheet for the selected samples")
            })

        for rv in self.review_states:
            rv.setdefault("custom_transitions", []).extend(custom_transitions)

    def get_copy_to_new_transition(self):
        """Returns the copy to new custom transition if the current has enough
        privileges. Returns None otherwise
        """
        base_url = None
        mtool = api.get_tool("portal_membership")
        if mtool.checkPermission(AddAnalysisRequest, self.context):
            base_url = self.url
        else:
            client = api.get_current_client()
            if client and mtool.checkPermission(AddAnalysisRequest, client):
                base_url = api.get_url(client)

        if base_url:
            return {
                "id": "copy_to_new",
                "title": _("Copy to new"),
                "url": "{}/workflow_action?action=copy_to_new".format(base_url)
            }

        return None

    def can_create_worksheet(self):
        """Checks if the create worksheet transition should be rendered or not
        """
        # check add permission for Worksheets
        if not can_add_worksheet(self.portal):
            return False

        # 没有选中任何样本时，不显示避免空选择也出现
        samples = list(self.get_selected_samples())
        if not samples:
            return False

        # 要求：样本必须在sample_received
        for sample in samples:
            state = api.get_workflow_status_of(sample)
            if state not in ["sample_received"]:
                return False

        # only available for samples in received state and with at least one
        # analysis in unassigned status
        # for sample in self.get_selected_samples():
        #     state = api.get_workflow_status_of(sample)
        #     if state not in ["sample_received"]:
        #         return False

        # # At least one analysis in unassigned status
        # if not self.has_unassigned_analyses(sample):
        #     return False

        # restrict contexts to well known places
        if ISamples.providedBy(self.context):
            return True
        elif IBatch.providedBy(self.context):
            return True
        elif IClient.providedBy(self.context):
            return True
        else:
            return False

    def has_unassigned_analyses(self, sample):
        """Returns whether the sample passed in has at least one analysis in
        'unassigned' status
        """
        for analysis in sample.getAnalyses():
            status = api.get_review_status(analysis)
            if status == "unassigned":
                return True
        return False

    def get_selected_samples(self):
        """Returns the selected samples
        """
        payload = self.get_json()
        uids = payload.get("selected_uids", [])
        return map(api.get_object, uids)

    @property
    def is_printing_workflow_enabled(self):
        setup = api.get_setup()
        return setup.getPrintingWorkflowEnabled()

    def str_date(self, date, long_format=1, default=""):
        if not date:
            return default
        return self.ulocalized_time(date, long_format=long_format)

    def to_datetime_input_value(self, date):
        """Converts to a compatible datetime format
        """
        if not isinstance(date, DateTime):
            return ""
        return dtime.date_to_string(date, fmt="%Y-%m-%d %H:%M")

    def getDefaultAddCount(self):
        return self.context.bika_setup.getDefaultNumberOfARsToAdd()

    @property
    def show_partitions(self):
        if self.flat_listing:
            return False
        if api.get_current_client():
            # If current user is a client contact, delegate to ShowPartitions
            return api.get_setup().getShowPartitions()
        return True

    @property
    def flat_listing(self):
        return self.review_state.get("flat_listing", False)

    @staticmethod
    def _download_url_from_obj(o):
        """把报告对象/brain转成下载URL。
        """
        try:
            if hasattr(o, "getObject"):
                o = o.getObject()
        except Exception:
            pass
        if not o:
            return u""

        try:
            base = o.absolute_url()
        except Exception:
            return u""

        try:
            if getattr(o, "restrictedTraverse", None) and o.restrictedTraverse("download_pdf", None):
                return base + "/download_pdf"
            if getattr(o, "download_pdf", None):
                return base + "/download_pdf"
        except Exception:
            pass

        for fname in ("file", "report", "Report", "report_file", "pdf"):
            try:
                if getattr(o, fname, None) is not None:
                    return base + "/@@download/%s" % fname
            except Exception:
                pass

        return base + "/at_download/file"

    @staticmethod
    def _pick_latest_report(seq):
        if not seq:
            return None
        if not isinstance(seq, (list, tuple)):
            seq = [seq]

        objs = []
        for x in seq:
            try:
                x = x.getObject() if hasattr(x, "getObject") else x
            except Exception:
                pass
            if x:
                objs.append(x)
        if not objs:
            return None

        import re
        def _key(o):
            # created / CreationDate / modified / ModificationDate
            for attr in ("created", "CreationDate", "modified", "ModificationDate"):
                try:
                    v = getattr(o, attr, None)
                    v = v() if callable(v) else v
                    if v:
                        return v
                except Exception:
                    pass
            try:
                m = re.search(r"(\d+)$", getattr(o, "id", "") or "")
                return int(m.group(1)) if m else 0
            except Exception:
                return 0

        objs.sort(key=_key, reverse=True)
        return objs[0]

    def _resolve_latest_report_pdf_via_report_catalog(self, ar_obj):
        try:
            rc = api.get_tool("senaite_catalog_report")
            if not rc:
                return u""

            ar_uid = api.get_uid(ar_obj) or u""
            ar_id = safe_unicode(getattr(ar_obj, "getId", lambda: "?")())

            brains = rc.searchResults(
                sample_uid=ar_uid,
                review_state="published",
                is_active=True,
                sort_on="created",
                sort_order="reverse",
                sort_limit=3,
            )

            if not brains:
                path = "/".join(ar_obj.getPhysicalPath())
                brains = rc.searchResults(
                    path={"query": path, "depth": 3},
                    review_state="published",
                    is_active=True,
                    sort_on="created",
                    sort_order="reverse",
                    sort_limit=3,
                )
            if not brains:
                return u""
            b = brains[0]
            try:
                obj = api.get_object(b)
            except Exception:
                obj = None

            if obj:
                url = self._download_url_from_obj(obj)
                return url

            if hasattr(b, "getURL"):
                url = safe_unicode(b.getURL())
                return url

            return u""
        except Exception as e:
            _log_exc(u"[Report][CAT] fatal: %r", e)
            return u""

    def _resolve_latest_report_pdf(self, ar_obj):
        ar_id = safe_unicode(getattr(ar_obj, "getId", lambda: "?")())
        try:
            url = self._resolve_latest_report_pdf_via_report_catalog(ar_obj)
            if url:
                return url

            getters = (
                "getLastPublishedReport", "getPublishedReports",
                "getReports", "getReport", "getLastReport", "getPDFReport",
            )

            for g in getters:
                fn = getattr(ar_obj, g, None)
                if not callable(fn):
                    continue
                res = fn()
                if isinstance(res, basestring) and res.strip():
                    return safe_unicode(res)
                latest = self._pick_latest_report(res)
                if latest:
                    url = self._download_url_from_obj(latest)
                    return url

            try:
                cat = getToolByName(ar_obj, "portal_catalog")
                path = "/".join(ar_obj.getPhysicalPath())
                brains = cat(path={"query": path, "depth": 3})
                pdfs = []
                for b in brains:
                    ctype = _lowerish(getattr(b, "ContentType", None)) or _lowerish(getattr(b, "content_type", None))
                    bid = _lowerish(getattr(b, "id", None))
                    bt = _lowerish(getattr(b, "Title", None))
                    if "pdf" in ctype or bid.endswith(".pdf") or bt.endswith(".pdf"):
                        pdfs.append(b)

                if pdfs:
                    def _bkey(br):
                        m = getattr(br, "modified", None) or getattr(br, "ModificationDate", None)
                        e = getattr(br, "effective", None)
                        return (m or e or 0)

                    pdfs.sort(key=_bkey, reverse=True)
                    b = pdfs[0]
                    try:
                        obj = b.getObject()
                    except Exception:
                        obj = None
                    if obj:
                        url = self._download_url_from_obj(obj)
                        return url
                    if hasattr(b, "getURL"):
                        url = safe_unicode(b.getURL())
                        return url
            except Exception as e:
                _log_exc(u"[Report] [PCAT] fallback failed: %r", e)
            return u""
        except Exception as e:
            _log_exc(u"[Report] resolve fatal for AR=%s: %r", ar_id, e)
            return u""
