# -*- coding: utf-8 -*-
import logging
from urllib import unquote
from urlparse import urlparse, parse_qs

from bika.lims import api
from senaite.core.browser.samples.view import SamplesView

logger = logging.getLogger("senaite.core.project_center_samples_filter")


class ProjectCenterSamplesFilterListingView(SamplesView):
    """独立的样本单条件过滤页（复用默认 SamplesView / ListingView）

    典型URL:
      /TCRx/projects/project-center-samples-filter
        ?filter_kind=cancer
        &filter_value=食管癌
        &filter_label=食管癌
        [&project=KSU03-R103]
        [&client_uid=xxxx]
        [&client_id=02]
    """

    FILTER_MAP = {
        "cancer": {
            "query_index": "getCancerType",
            "label": u"癌种",
        },
        "sample_type": {
            "query_index": "getSampleTypeUID",
            "label": u"样本类型",
        },
        "creator": {
            "query_index": "Creator",
            "label": u"创建者",
        },
    }

    def __init__(self, context, request):
        super(ProjectCenterSamplesFilterListingView, self).__init__(context, request)
        self.filter_kind = u""
        self.filter_value = u""
        self.filter_label = u""

        self.project = u""
        self.client_uid = u""
        self.client_id = u""

    # -----------------------------
    # 参数读取
    # -----------------------------
    def _safe_u(self, v, default=u""):
        if v is None:
            return default
        try:
            if isinstance(v, (list, tuple)):
                v = v[0] if v else default
        except Exception:
            pass
        try:
            return api.safe_unicode(unquote(v)).strip()
        except Exception:
            try:
                return api.safe_unicode(v).strip()
            except Exception:
                return default

    def _catalog_safe_term(self, v, default=""):
        try:
            if v is None:
                return default
            if isinstance(v, (list, tuple)):
                v = v[0] if v else default
                if v is None:
                    return default
            if isinstance(v, str):
                return v.strip()

            if isinstance(v, unicode):
                return v.strip().encode("utf-8")

            return api.safe_unicode(v).strip().encode("utf-8")
        except Exception:
            try:
                return str(v).strip()
            except Exception:
                return default

    def _reqv(self, key, default=u""):
        v = None
        try:
            v = self.request.form.get(key, None)
        except Exception:
            v = None
        if v is None:
            try:
                v = self.request.get(key, None)
            except Exception:
                v = None
        else:
            try:
                if isinstance(v, unicode):
                    is_empty = (v.strip() == u"")
                elif isinstance(v, str):
                    is_empty = (v.strip() == "")
                else:
                    is_empty = (api.safe_unicode(v).strip() == u"")
            except Exception:
                is_empty = False

            if is_empty:
                try:
                    v = self.request.get(key, None)
                except Exception:
                    v = None
        return self._safe_u(v, default)

    def _load_params(self):
        self.filter_kind = self._reqv("filter_kind", u"")
        self.filter_value = self._reqv("filter_value", u"")
        self.filter_label = self._reqv("filter_label", u"") or self.filter_value
        self.project = self._reqv("project", u"")
        self.client_uid = self._reqv("client_uid", u"")
        self.client_id = self._reqv("client_id", u"")

    def _load_params_from_referer(self):
        try:
            referer = self.request.get("HTTP_REFERER", "") or ""
        except Exception:
            referer = ""

        if not referer:
            return

        try:
            parsed = urlparse(referer)
            qs = parse_qs(parsed.query, keep_blank_values=True)
        except Exception:
            return

        def _q(name):
            vals = qs.get(name) or []
            if not vals:
                return u""
            return self._safe_u(vals[0], u"")

        if not self.filter_kind:
            self.filter_kind = _q("filter_kind")
        if not self.filter_value:
            self.filter_value = _q("filter_value")
        if not self.filter_label:
            self.filter_label = _q("filter_label") or self.filter_value

        if not self.project:
            self.project = _q("project")
        if not self.client_uid:
            self.client_uid = _q("client_uid")
        if not self.client_id:
            self.client_id = _q("client_id")

    def update(self):
        # 先走 SamplesView 默认逻辑（listing 初始化）
        super(ProjectCenterSamplesFilterListingView, self).update()

        # 再读我们自己的参数
        self._load_params()
        self._load_params_from_referer()
        # 标题（显示在默认页面标题区域）
        title = u"样本筛选结果"
        parts = []
        if self.project:
            parts.append(self.project)

        kind_label = self.get_filter_label_name()
        if self.filter_kind and self.filter_value:
            parts.append(u"%s：%s" % (kind_label, self.filter_label or self.filter_value))

        if parts:
            title = u" ｜ ".join(parts)

        try:
            self.title = title
        except Exception:
            pass

        # 关键：把参数写回 request.form（避免分页/排序/Ajax时丢失）
        try:
            if self.filter_kind:
                self.request.form["filter_kind"] = self.filter_kind
            if self.filter_value:
                self.request.form["filter_value"] = self.filter_value
            if self.filter_label:
                self.request.form["filter_label"] = self.filter_label

            if self.project:
                self.request.form["project"] = self.project
            if self.client_uid:
                self.request.form["client_uid"] = self.client_uid
            if self.client_id:
                self.request.form["client_id"] = self.client_id
        except Exception:
            pass

    def get_filter_conf(self):
        return self.FILTER_MAP.get(self.filter_kind, {})

    def get_filter_label_name(self):
        conf = self.get_filter_conf() or {}
        return conf.get("label", u"筛选字段")

    def get_catalog_query(self, *args, **kwargs):
        query = super(ProjectCenterSamplesFilterListingView, self).get_catalog_query(*args, **kwargs)

        if self.client_uid:
            query["getClientUID"] = self.client_uid

        if self.client_id:
            query["getClientID"] = self.client_id

        if self.project:
            query["getProjectName"] = self.project

        conf = self.get_filter_conf()
        idx = conf.get("query_index")
        if idx and self.filter_value:
            query[idx] = self._catalog_safe_term(self.filter_value)
        return query

    def folderitems(self):
        items = super(ProjectCenterSamplesFilterListingView, self).folderitems()
        if not items:
            return items

        target_kind = api.safe_unicode(self.filter_kind or u"").strip()
        target_value = api.safe_unicode(self.filter_value or u"").strip()

        if not target_kind or not target_value:
            return []

        return items
