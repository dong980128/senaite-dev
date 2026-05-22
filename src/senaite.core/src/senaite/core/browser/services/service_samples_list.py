# -*- coding: utf-8 -*-
#
# ServiceSamplesListView
#
# 按“检验项目(Analysis Service)”过滤后的样本列表视图
# URL 形如：
#   @@service-sample-list?service_uid=<AnalysisService 的 UID>
#

import logging

from urlparse import urlparse, parse_qs

from bika.lims import _
from bika.lims import api
from Products.CMFPlone.utils import safe_unicode

from senaite.core.browser.samples.view import SamplesView

logger = logging.getLogger("senaite.core")

def _extract_service_uid_from_referer(request):
    """当本次请求没带 service_uid 时，从 Referer 里尝试解析"""

    try:
        ref = request.get_header("referer", "") or ""
    except Exception:
        ref = ""

    if not ref:
        return u""

    try:
        parsed = urlparse(ref)
        qs = parse_qs(parsed.query)
        uid_list = qs.get("service_uid", []) or []
        uid = (uid_list[0] if uid_list else u"").strip()
    except Exception:
        return u""

    return uid


class ServiceSamplesListView(SamplesView):

    def __init__(self, context, request):
        super(ServiceSamplesListView, self).__init__(context, request)

        self.service_uid = (request.get("service_uid", "") or "").strip()
        self._service = None
        self.service_title = u""

        if not self.service_uid:
            ref_uid = _extract_service_uid_from_referer(request)
            if ref_uid:
                self.service_uid = ref_uid

        if self.service_uid:
            try:
                self._service = api.get_object_by_uid(self.service_uid)
            except Exception as e:
                self._service = None

        # Service 标题（含中文，必须 safe_unicode）
        if self._service:
            try:
                self.service_title = safe_unicode(self._service.Title())
            except Exception as e:
                self.service_title = u""

        # 页面标题
        if self.service_title:
            self.title = u"检验项目样本列表：%s" % self.service_title
        else:
            self.title = u"检验项目样本列表"

        portal = api.get_portal()
        portal_url = api.get_url(portal)
        back_url = "%s/@@service-samples" % portal_url

        # 不用 _() 包裹中文，避免再次编码问题
        self.context_actions = {
            u"返回检验项目列表": {
                "url": back_url,
                "icon": "glyphicon glyphicon-arrow-left",
            }
        }

    def _get_root_sample(self, ar):
        if ar is None:
            return None

        candidate_methods = (
            "getRoot",
            "getRootSample",
            "getRootAR",
            "getRootAnalysisRequest",
        )

        for name in candidate_methods:
            func = getattr(ar, name, None)
            if callable(func):
                try:
                    root = func()
                except Exception:
                    root = None
                else:
                    if root is not None:
                        return root

        return ar

    def _sample_uids_for_service(self):
        """返回当前 service 对应的“有效分析”所在【根样本】UID 集合(set)。"""

        if not self.service_uid:
            return set()

        ac = api.get_tool("senaite_catalog_analysis") or \
             api.get_tool("bika_analysis_catalog")

        if not ac:
            logger.warning(
                u"[ServiceSamplesListView] analysis catalog not found"
            )
            return set()

        analysis_states = self._analysis_states_for_current_tab()

        try:
            brains = ac(
                portal_type="Analysis",
                getServiceUID=self.service_uid,
                review_state=analysis_states,
            )
        except Exception as e:
            return set()

        sample_uids = set()

        for i, brain in enumerate(brains):
            try:
                analysis = brain.getObject()
            except Exception as e:
                continue

            ar = getattr(analysis, "aq_parent", None)
            if not ar:
                continue

            root_sample = self._get_root_sample(ar) or ar

            try:
                root_uid = root_sample.UID()
            except Exception:
                root_uid = u""

            if root_uid:
                sample_uids.add(root_uid)

        return sample_uids

    # ----------------------------------------------------------------------
    # 覆盖 SamplesView 的 get_catalog_query，在查询里加上 UID 过滤
    # ----------------------------------------------------------------------
    def get_catalog_query(self, *args, **kwargs):
        """在原有 SamplesView 查询基础上，增加按 service 过滤的 UID 条件。"""

        query = super(ServiceSamplesListView, self).get_catalog_query(
            *args, **kwargs
        )

        if not self.service_uid:
            return query

        sample_uids = self._sample_uids_for_service()

        if not sample_uids:
            query["UID"] = ["__no_such_uid__"]
        else:
            query["UID"] = list(sample_uids)

        return query

    def _analysis_states_for_current_tab(self):
        """根据当前 SamplesView 页签，决定要包含哪些 Analysis 状态"""
        cur_id = (self.review_state or {}).get("id")  # SamplesView 里已有

        if cur_id == "to_be_verified":
            return ("to_be_verified",)

        if cur_id == "verified":
            return ("verified",)

        if cur_id in ("default", "sample_received"):
            return ("sample_received",)

        if cur_id == "cancelled":
            return ("cancelled",)

        if cur_id in ("invalid", "rejected"):
            return ("rejected", "retracted")

        # 其它页签就按“全部有效状态”来
        return (
            "unassigned",
            "assigned",
            "sample_received",
            "to_be_verified",
            "verified",
            "published",
        )
