# -*- coding: utf-8 -*-
from cgi import escape

from bika.lims import api
from senaite.app.listing import ListingView


class LabProcessesView(ListingView):

    def __init__(self, context, request):
        super(LabProcessesView, self).__init__(context, request)
        self.title = u"Lab Processes"
        self.description = u"All LabProcess items"
        self.pagesize = 50

        self.columns = {
            "Title": {
                "title": u"流程",
                "index": "sortable_title"},
            "Version": {
                "title": u"版本"},
            "Modified": {
                "title": u"修改时间",
                "index": "modified"},
        }

        self.review_states = [{
            "id": "default",
            "title": u"全部",
            "contentFilter": {
                "portal_type": "LabProcess",
                "sort_on": "modified",
                "sort_order": "descending",
            },
            "columns": self.columns.keys(),
        }]


    def folderitems(self):
        items = super(LabProcessesView, self).folderitems()

        for it in items:
            obj = it.get("obj")
            if not obj:
                continue

            it["Version"] = getattr(obj, "getVersion", lambda: u"")()
            title = it.get("Title", u"")
            url = it.get("url") or api.get_url(obj)
            it.setdefault("replace", {})
            it["replace"]["Title"] = u'<a href="{0}">{1}</a>'.format(
                url, escape(api.safe_unicode(title))
            )

        return items
