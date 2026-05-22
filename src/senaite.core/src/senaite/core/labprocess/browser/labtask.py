# -*- coding: utf-8 -*-
from bika.lims import api
from senaite.app.listing import ListingView

def _id(obj):
    v = getattr(obj, "getId", None)
    if callable(v):
        return v()
    v = getattr(obj, "id", None)
    if callable(v):
        return v()
    return v or u"?"

def _as_list(v):
    if v is None:
        return []
    # 字符串：可能是逗号分隔，也可能是单值
    if isinstance(v, basestring):
        s = v.strip()
        if not s:
            return []
        # 支持 "a,b,c" / "a, b, c"
        if "," in s:
            return [x.strip() for x in s.split(",") if x.strip()]
        return [s]
    # 已经是 list/tuple/set
    if isinstance(v, (list, tuple, set)):
        return list(v)
    return [v]

class LabTasksView(ListingView):

    def __init__(self, context, request):
        super(LabTasksView, self).__init__(context, request)
        self.title = u"Lab Tasks"
        self.description = u"Tasks under this LabProcess"
        self.pagesize = 50

        # 列（注意：Actions 必须是 dict）
        self.columns = {
            "Title": {"title": u"任务", "index": "sortable_title"},
            "Step": {"title": u"Step"},
            "StageName": {"title": u"Stage"},
            "Users": {"title": u"Users"},
            "Services": {"title": u"Services"},
            "Analyses": {"title": u"Analyses"},
            "ResultSummary": {"title": u"Result"},
            "Actions": {"title": u"操作"},
        }

        self._col_order = [
            "ResultSummary",
            "Users",
            "StageName",
            "Title",
            "Step",
            "Services",
            "Analyses",
            "Actions",
        ]

        self.review_states = [{
            "id": "default",
            "title": u"全部",
            "contentFilter": self.get_content_filter(),
            "columns": self._col_order,
        }]

    def get_content_filter(self):
        path = "/".join(self.context.getPhysicalPath())
        return {
            "portal_type": "LabTask",
            "path": {"query": path, "depth": 1},
            "sort_on": "sortable_title",
        }

    def folderitems(self):
        items = super(LabTasksView, self).folderitems()
        for it in items:
            brain = it.get("obj")
            if not brain:
                continue

            obj = api.get_object(brain) or brain

            tid = getattr(obj, "getId", None)
            tid = tid() if callable(tid) else getattr(obj, "id", u"?")

            step = self._get(obj, "step", u"")
            stage_name = self._get(obj, "stage_name", u"")
            users = _as_list(self._get(obj, "assigned_users", []))
            services = _as_list(self._get(obj, "services", []))

            it["Step"] = step or u""
            it["StageName"] = stage_name or u""
            it["Users"] = u", ".join([api.safe_unicode(x) for x in users])
            it["Services"] = u", ".join([api.safe_unicode(x) for x in services])

            # Analyses links：UID -> object
            uids = getattr(obj, "getAnalysisUIDs", lambda: [])() or []
            links = []
            for uid in uids:
                a = api.get_object_by_uid(uid)
                if not a:
                    continue
                links.append(u'<a href="%s">%s</a>' % (a.absolute_url(), api.safe_unicode(a.Title())))
            it["Analyses"] = u", ".join(links)

        return items

    def _get(self, obj, name, default=None):
        if hasattr(obj, "accessor"):
            try:
                val = obj.accessor(name)(obj)
                return default if val in (None, u"") else val
            except Exception:
                pass

        val = getattr(obj, name, default)
        return val