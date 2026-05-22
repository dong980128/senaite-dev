# -*- coding: utf-8 -*-
from collections import OrderedDict

from senaite.app.listing import ListingView
from bika.lims import api
from bika.lims import bikaMessageFactory as _


class LabProcessesListingView(ListingView):

    def __init__(self, context, request):
        super(LabProcessesListingView, self).__init__(context, request)

        self.title = _("Lab Processes")
        self.description = _("All LabProcess items")

        self.show_select_column = True
        self.show_search = True
        self.pagesize = 50

        self.catalog = "portal_catalog"
        self.contentFilter = {
            "portal_type": "LabProcess",
            "sort_on": "modified",
            "sort_order": "reverse",
        }

        self.context_actions = OrderedDict((
            (_(u"添加"), {
                "url": "{0}/@@labprocess-add".format(api.get_url(context)),
                "icon": "++resource++bika.lims.images/add.png",
            }),
        ))

        # 列：流程 / 关键词 / 状态 / 版本 / 修改时间
        self.columns = OrderedDict((
            ("title", {"title": _(u"流程"), "index": "sortable_title"}),
            ("process_key", {"title": _(u"关键词")}),
            ("review_state", {"title": _(u"状态"), "index": "review_state"}),
            ("version", {"title": _(u"版本")}),
            ("modified", {"title": _(u"修改时间"), "index": "modified"}),
        ))

        # 关键：一定要保留 id="default"，否则 URL 里 default 会导致 500
        self.review_states = [
            {
                "id": "default",
                "title": _(u"正在进行"),
                "contentFilter": {"portal_type": "LabProcess", "review_state": "active"},
                "columns": ["title", "process_key", "version", "modified"],
            },
            {
                "id": "inactive",
                "title": _(u"停用"),
                "contentFilter": {"portal_type": "LabProcess", "review_state": "inactive"},
                "columns": ["title", "process_key", "version", "modified"],
            },
            {
                "id": "all",
                "title": _(u"所有"),
                "contentFilter": {"portal_type": "LabProcess"},
                "columns": ["title", "process_key", "review_state", "version", "modified"],
            },
        ]

    def folderitem(self, brain, item, index):
        obj = brain.getObject()

        # title 链接到 @@process
        title = api.get_title(obj) or obj.getId()
        item["title"] = title
        item.setdefault("replace", {})
        item["replace"]["title"] = u'<a href="{0}">{1}</a>'.format(
            obj.absolute_url() + "/@@process",
            api.safe_unicode(title),
        )

        # 关键词
        key = getattr(brain, "process_key", "") or ""
        if not key:
            try:
                key = getattr(obj, "process_key", "") or ""
            except Exception:
                key = ""
        item["process_key"] = api.safe_unicode(key) if key else ""

        # 状态
        item["review_state"] = getattr(brain, "review_state", "") or ""

        # 版本
        get_version = getattr(obj, "getVersion", None)
        item["version"] = get_version() if callable(get_version) else ""

        # 修改时间
        item["modified"] = brain.modified
        return item
