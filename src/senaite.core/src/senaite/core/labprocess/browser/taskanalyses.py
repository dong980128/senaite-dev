# -*- coding: utf-8 -*-

import logging

logger = logging.getLogger(__name__)
from bika.lims.browser.analyses import AnalysesView
from Products.CMFPlone.utils import safe_unicode


class LabProcessTaskAnalysesView(AnalysesView):
    def __init__(self, context, request, **kwargs):
        super(LabProcessTaskAnalysesView, self).__init__(context, request, **kwargs)
        try:
            self.show_select_column = False
        except Exception:
            pass
        pass

    def _req_get(self, key, default=u""):
        """Read from request.form -> JSON body -> request.other -> request.get"""
        req = self.request
        try:
            if key in req.form:
                return req.form.get(key)
        except Exception:
            pass

        try:
            body = getattr(req, "_json_body_cache", None)
            if body is None:
                import json
                raw = getattr(req, "BODY", b"") or b""
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="ignore")
                body = json.loads(raw) if raw.strip().startswith("{") else {}
                req._json_body_cache = body
            if key in body:
                return body[key]
        except Exception as _e:
            pass

        try:
            other = getattr(req, "other", {}) or {}
            if key in other:
                return other.get(key)
        except Exception:
            pass

        try:
            return req.get(key, default)
        except Exception:
            return default

    def _get_form_id(self):
        fid = safe_unicode(self._req_get("form_id", u"") or u"").strip()
        if fid:
            return fid
        return safe_unicode(getattr(self, "form_id", u"analyses_form") or u"analyses_form")

    def _parse_uids(self, raw):
        if raw is None:
            return []
        if isinstance(raw, (list, tuple)):
            raw = u",".join([safe_unicode(x) for x in raw if x])
        raw = safe_unicode(raw or u"")
        out = []
        for part in raw.split(u","):
            p = safe_unicode(part or u"").strip()
            if p:
                out.append(p)
        return out

    def _get_current_review_state_id(self):

        for key in ("review_state", "ReviewState", "state", "tab"):
            rs = self._req_get(key, None)
            if rs and rs != u"default":
                return safe_unicode(rs).strip()

        try:
            req = self.request
            form = dict(getattr(req, "form", {}) or {})
        except Exception:
            pass
        return u"default"

    def _get_analysis_uids_from_taskrun(self):
        """当 analysis_uids 参数丢失时（tab 切换），从 taskrun_uid 反查"""
        fid = self._get_form_id()
        task_uid = self._req_get(u"%s_taskrun_uid" % fid, None)
        if not task_uid:
            task_uid = self._req_get(u"taskrun_uid", None)
        if not task_uid:
            return []
        try:
            from bika.lims import api
            tr = api.get_object_by_uid(task_uid, default=None)
            if tr is None:
                return []
            uids = [x for x in (getattr(tr, "analysis_uids", None) or []) if x]
            return [safe_unicode(u).strip() for u in uids if u]
        except Exception:
            return []

    def _apply_task_uid_filter(self):
        fid = self._get_form_id()
        self.form_id = fid

        key = u"%s_analysis_uids" % fid
        raw = self._req_get(key, None)
        if raw in (None, u"", ""):
            raw = self._req_get("analysis_uids", u"")

        uids = self._parse_uids(raw)
        if not uids:
            uids = self._get_analysis_uids_from_taskrun()

        if not uids:
            return []

        rs_id = self._get_current_review_state_id()
        try:
            req = self.request
            qs = getattr(req, "QUERY_STRING", "")
            form = dict(getattr(req, "form", {}) or {})
            other = dict(getattr(req, "other", {}) or {})
        except Exception:
            pass
        if rs_id in (u"invalid", u"all"):
            return uids

        # default tab（有效）：只显示当前 analysis_uids 里的对象
        self.contentFilter["UID"] = {"query": uids}
        return uids

    def get_api_url(self):
        base = self.context.absolute_url()
        name = safe_unicode(getattr(self, "__name__", u"") or u"").strip()

        if not name:
            name = u"@@labprocess_task_analyses"
        elif not name.startswith(u"@@"):
            name = u"@@%s" % name

        return u"{}/{}".format(base, name)

    def update(self):
        super(LabProcessTaskAnalysesView, self).update()

        self.review_states = [
            {
                "id": "default",
                "title": u"有效",
                "contentFilter": {
                    "review_state": [
                        "registered",
                        "unassigned",
                        "assigned",
                        "to_be_verified",
                        "verified",
                        "published",
                    ]
                },
                "columns": self.columns.keys(),
            },
            {
                "id": "invalid",
                "title": u"无效",
                "contentFilter": {
                    "review_state": [
                        "verified",
                        "retracted",
                        "rejected",
                        "cancelled",
                    ]
                },
                "columns": self.columns.keys(),
            },
            {
                "id": "all",
                "title": u"所有",
                "contentFilter": {},
                "columns": self.columns.keys(),
            },
        ]

        uids = self._apply_task_uid_filter()
        return

    def _reorder_columns(self):
        try:
            from collections import OrderedDict
            # 固定列（非interim结果字段）顺序
            fixed = [
                u'created',
                u'Service',
                u'Analyst',
                u'DetectionLimitOperand',
                u'Uncertainty',
                u'Unit',
                u'Specification',
                u'retested',
                u'Method',
                u'Instrument',
                u'Calculation',
                u'Attachments',
                u'SubmittedBy',
                u'ResultCaptureDate',
                u'DueDate',
                u'state_title',
                u'Hidden',
            ]

            fixed_set = set(fixed)
            cols = self.columns

            TCR_TYPES={"tcr_selector", "tcr_preparation"}
            interims = list(reversed([
                k for k in cols.keys()
                if k not in fixed_set
                and cols[k].get("type") not in TCR_TYPES
            ]))

            insert_before = u'DetectionLimitOperand'
            new_order = []
            for k in fixed:
                if k == insert_before:
                    new_order.extend(interims)
                new_order.append(k)
            new_cols = OrderedDict()
            for k in new_order:
                if k in cols:
                    new_cols[k] = cols[k]
            for k, v in cols.items():
                if k not in new_cols:
                    new_cols[k] = v
            self.columns = new_cols

            # TableHeaderRow 和 TableCells 才能识别并隐藏这些列头/列
            try:
                from bika.lims.browser.analyses.view import AnalysesView as _AV
                grouped_cfg = getattr(_AV, "LP_GROUPED_FIELDS_CONFIG", {})
                grouped_keywords = set()
                for groups in grouped_cfg.values():
                    for group in groups:
                        for f in group.get("fields", []):
                            grouped_keywords.add(f["keyword"])
                for kw in grouped_keywords:
                    if kw in self.columns:
                        self.columns[kw]["type"] = "grouped_fields"
            except Exception:
                pass

            col_list = [k for k in new_cols.keys() if k != u'created']
            for rs in getattr(self, "review_states", []):
                rs["columns"] = col_list
        except Exception:
            pass

    def _get_latest_uids(self, uids):
        """ 因为在Task中的Analyses 走的状态跳过复核状态，直接从提交->复核,默认复核和复测都是在有效中显示，但是在这里
            把retestied的检测项目放到无效中(invalid)，下面是处理逻辑
        """
        if not uids:
            return uids
        try:
            from bika.lims import api as _api
            ac = _api.get_tool("senaite_catalog_analysis")
            uid_set = set(uids)
            superseded = set()
            for uid in uids:
                brains = ac(UID=uid)
                if not brains:
                    continue
                obj = brains[0].getObject()
                parent = getattr(obj, "getRetestOfUID", lambda: None)()
                if parent and parent in uid_set:
                    superseded.add(parent)
            latest = [u for u in uids if u not in superseded]
            return latest if latest else uids
        except Exception:
            return uids

    def folderitems(self, **kw):
        uids = self._apply_task_uid_filter()
        if not uids:
            self.contentFilter["UID"] = {"query": [u"__none__"]}
        # 强制当前tab的review_state，所有tab都限制UID范围
        rs_id = self._get_current_review_state_id()
        for rs in (self.review_states or []):
            if rs.get("id") == rs_id:
                tab_rs = rs.get("contentFilter", {}).get("review_state")
                if tab_rs:
                    self.contentFilter["review_state"] = tab_rs
                if uids:
                    if rs_id == u"default":
                        latest = self._get_latest_uids(uids)
                        self.contentFilter["UID"] = {"query": latest or [u"__none__"]}
                        # self.contentFilter["UID"] ={"query": uids}
                    else:

                        # invalid/all tab：显示全部TaskRun uids不过滤链末端
                        self.contentFilter["UID"] = {"query": uids}
                elif "review_state" in self.contentFilter:
                    del self.contentFilter["review_state"]
                break
        # 返回当前类的folder items
        items = super(LabProcessTaskAnalysesView, self).folderitems(**kw)
        self._reorder_columns()
        return items