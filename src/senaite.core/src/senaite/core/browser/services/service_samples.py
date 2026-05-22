# -*- coding: utf-8 -*-
from Products.Five import BrowserView
from Products.CMFCore.utils import getToolByName

class ServiceBaseView(BrowserView):
    """共用的一些工具方法"""

    def _get_setup_catalog(self):
        """返回 setup catalog，用于查询 AnalysisService"""
        context = self.context
        for name in ("senaite_catalog_setup",
                     "bika_setup_catalog",
                     "portal_catalog"):
            cat = getToolByName(context, name, None)
            if cat is not None:
                return cat
        return None

    def _get_analysis_catalog(self):
        """返回 analysis catalog，用于统计样本"""
        context = self.context
        for name in ("senaite_catalog_analysis",
                     "bika_analysis_catalog"):
            cat = getToolByName(context, name, None)
            if cat is not None:
                return cat
        return None

    # ---------- 无效实验状态过滤 ----------

    # >>> 注意：元组里每个元素都要有逗号，否则会变成一个长字符串
    INVALID_ANALYSIS_STATES = (
        "rejected",
        "retracted",
        # 如果以后 "cancelled" 也算无效，可以在这里再加一个:
        # "cancelled",
    )

    def _is_valid_analysis_brain(self, brain):
        state = getattr(brain, "review_state", None)
        if not state:
            return True

        if state in self.INVALID_ANALYSIS_STATES:
            return False

        return True

class ServiceSamplesView(ServiceBaseView):

    def active_filter(self):
        """从请求参数中读取当前过滤状态"""
        value = self.request.get("active", "active")
        if value not in ("active", "inactive", "all"):
            value = "active"
        return value

    def services(self):
        """返回过滤后的 AnalysisService brains 列表"""
        catalog = self._get_setup_catalog()
        flt = self.active_filter()

        if catalog is None:
            return []

        query = {
            "portal_type": "AnalysisService",
            "sort_on": "sortable_title",
        }

        # 使用 is_active 字段做启用/停用过滤
        if flt == "active":
            query["is_active"] = True
        elif flt == "inactive":
            query["is_active"] = False
        # flt == "all" 不加 is_active 条件

        try:
            brains = catalog(**query)
        except Exception:
            return []

        return brains

    # ---------- 样本数量 ----------

    def sample_count(self, svc_brain):

        ac = self._get_analysis_catalog()
        if ac is None or svc_brain is None:
            return 0

        svc_uid = getattr(svc_brain, "UID", None)
        if not svc_uid:
            return 0

        # 使用 getServiceUID 索引快速查询对应的 Analysis
        try:
            brains = ac(getServiceUID=svc_uid, portal_type="Analysis")
        except Exception:
            return 0

        sample_uids = set()

        for b in brains:
            if not self._is_valid_analysis_brain(b):
                continue

            try:
                an = b.getObject()
            except Exception as e:
                continue

            # Analysis 的父对象就是样本(AnalysisRequest)
            ar = getattr(an, "aq_parent", None)
            if ar is None:
                continue

            try:
                ar_uid = ar.UID()
            except Exception:
                continue

            if ar_uid:
                sample_uids.add(ar_uid)

        count = len(sample_uids)
        return count

class ServiceSampleListView(ServiceBaseView):
    """某个检验项目对应的样本列表
    URL: @@service-sample-list?service_uid=<UID>
    """

    def service_uid(self):
        uid = self.request.get("service_uid", "").strip()
        return uid

    def service_brain(self):
        uid = self.service_uid()
        if not uid:
            return None
        sc = self._get_setup_catalog()
        if sc is None:
            return None
        brains = sc(UID=uid)
        return brains and brains[0] or None

    def service_title(self):
        brain = self.service_brain()
        if brain is None:
            return u""
        return brain.Title

    def samples(self):
        """返回样本列表：[{id, title, url}, ...]"""
        ac = self._get_analysis_catalog()
        svc_uid = self.service_uid()
        if ac is None or not svc_uid:
            return []

        try:
            brains = ac(getServiceUID=svc_uid, portal_type="Analysis")
        except Exception as e:
            return []

        sample_map = {}

        for b in brains:
            if not self._is_valid_analysis_brain(b):
                continue

            try:
                an = b.getObject()
            except Exception:
                continue

            ar = getattr(an, "aq_parent", None)
            if ar is None:
                continue

            try:
                ar_uid = ar.UID()
            except Exception:
                continue

            if not ar_uid or ar_uid in sample_map:
                continue

            # 样本 ID/标题/URL
            sample_id = None
            getter = getattr(ar, "getRequestID", None)
            if callable(getter):
                sample_id = getter()
            if not sample_id:
                sample_id = ar.getId()

            title = None
            t = getattr(ar, "Title", None)
            if callable(t):
                title = t()
            if not title:
                title = sample_id

            url = ar.absolute_url()

            sample_map[ar_uid] = {
                "id": sample_id,
                "title": title,
                "url": url,
            }

        # 按样本 ID 排序
        samples = sorted(sample_map.values(), key=lambda x: x["id"])
        return samples