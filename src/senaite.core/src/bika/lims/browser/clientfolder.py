# -*- coding: utf-8 -*-
import collections
import logging

from bika.lims import api
from bika.lims import bikaMessageFactory as _
from bika.lims.utils import check_permission
from bika.lims.utils import get_email_link
from bika.lims.utils import get_link
from Products.CMFCore.permissions import ModifyPortalContent
from Products.CMFCore.utils import getToolByName
from senaite.app.listing import ListingView
from senaite.core.catalog import CLIENT_CATALOG
from senaite.core.config.registry import CLIENT_LANDING_PAGE
from senaite.core.permissions import AddClient
from senaite.core.permissions import ManageAnalysisRequests
from senaite.core.registry import get_registry_record
from plone import api as ploneapi

logger = logging.getLogger()

class ClientFolderContentsView(ListingView):
    """Listing view for all Clients
    """

    def __init__(self, context, request):
        super(ClientFolderContentsView, self).__init__(context, request)

        self.title = self.context.translate(_("Clients"))
        self.description = ""
        self.form_id = "list_clientsfolder"
        self.sort_on = "sortable_title"

        self.catalog = CLIENT_CATALOG
        self.contentFilter = {
            "portal_type": "Client",
            "sort_on": "sortable_title",
            "sort_order": "ascending"
        }

        self.icon = "{}/{}".format(
            self.portal_url, "++resource++bika.lims.images/client_big.png")

        self.columns = collections.OrderedDict((
            ("title", {
                "title": _("Name"),
                "index": "sortable_title"
            }),
            ("ClientID", {
                "title": _("Client ID")}),
            ("ProjectNames", {
                "title": _("Projects"),
                "sortable": False}),
            ("ProjectTotal", {
                "title": _(u"项目数量"),
                "sortable": True,
            }),
            ("SampleTotal", {
                'title': _(u"样本数量"),
                'sortable': True,
            }),
            ("EmailAddress", {
                "title": _("Email Address"),
                "sortable": False}),
            ("Country", {
                "toggle": False,
                "sortable": False,
                "title": _("Country")}),
            ("Province", {
                "toggle": False,
                "sortable": False,
                "title": _("Province")}),
            ("District", {
                "toggle": False,
                "sortable": False,
                "title": _("District")}),
            ("Phone", {
                "title": _("Phone"),
                "sortable": False}),
            ("Fax", {
                "toggle": False,
                "sortable": False,
                "title": _("Fax")}),
            ("BulkDiscount", {
                "toggle": False,
                "sortable": False,
                "title": _("Bulk Discount")}),
            ("MemberDiscount", {
                "toggle": False,
                "sortable": False,
                "title": _("Member Discount")}),
        ))

        self.review_states = [
            {
                "id": "default",
                "contentFilter": {"review_state": "active"},
                "title": _("Active"),
                "transitions": [{"id": "deactivate"}, ],
                "columns": self.columns.keys(),
            }, {
                "id": "inactive",
                "title": _("Inactive"),
                "contentFilter": {"review_state": "inactive"},
                "transitions": [{"id": "activate"}, ],
                "columns": self.columns.keys(),
            }, {
                "id": "all",
                "title": _("All"),
                "contentFilter": {},
                "transitions": [],
                "columns": self.columns.keys(),
            },
        ]

    def before_render(self):
        """Before template render hook
        """
        super(ClientFolderContentsView, self).before_render()
        # Landing page to be added to the link of each client from the list
        self.landing_page = get_registry_record(CLIENT_LANDING_PAGE)

        # Render the Add button if the user has the AddClient permission
        if check_permission(AddClient, self.context):
            self.context_actions[_("Add")] = {
                "url": "createObject?type_name=Client",
                "icon": "++resource++bika.lims.images/add.png"
            }

        # Display a checkbox next to each client in the list if the user has
        # rights for ModifyPortalContent
        self.show_select_column = check_permission(ModifyPortalContent,
                                                   self.context)

    def isItemAllowed(self, obj):
        """Returns true if the current user has Manage AR rights for the
        current Client (item) to be rendered.
        """
        return check_permission(ManageAnalysisRequests, obj)

    def folderitem(self, obj, item, index):
        """Applies new properties to the item (Client) that is currently being
        rendered as a row in the list
        """
        obj = api.get_object(obj)
        client_uid = obj.UID()

        # render a link to the defined start page
        link_url = "{}/{}".format(item["url"], self.landing_page)
        item["replace"]["title"] = get_link(link_url, item["title"])

        # Client ID
        client_id = obj.getClientID()
        item["ClientID"] = client_id
        if client_id:
            item["replace"]["ClientID"] = get_link(link_url, client_id)

        # Email address
        email = obj.getEmailAddress()
        item["EmailAddress"] = get_email_link(email)
        if email:
            item["replace"]["EmailAddress"] = get_email_link(email)

        # Country, Province, District
        item["Country"] = obj.getCountry()
        item["Province"] = obj.getProvince()
        item["District"] = obj.getDistrict()

        # Phone
        phone = obj.getPhone()
        item["Phone"] = phone
        if phone:
            item["replace"]["Phone"] = get_link("tel:{}".format(phone), phone)

        # Fax
        item["Fax"] = obj.getFax()

        # Bulk Discount
        bulk_discount = obj.getBulkDiscount()
        bulk_discount_value = _("Yes") if bulk_discount else _("No")
        item["replace"]["BulkDiscount"] = bulk_discount_value

        # Member Discount
        member_discount = obj.getMemberDiscountApplies()
        member_discount_value = _("Yes") if member_discount else _("No")
        item["replace"]["MemberDiscount"] = member_discount_value
        item["ProjectNames"] = self._project_names_for_client(client_uid)

        # 仍然先保留原始数值
        total_projects = self._project_total_for_client(client_uid)
        sample_total = self._sample_total_for_client(client_uid)

        item["ProjectTotal"] = total_projects
        item["SampleTotal"] = sample_total

        item["sort_ProjectTotal"] = int(total_projects)
        item["sort_SampleTotal"] = int(sample_total)
        # 只替换显示为链接（不影响排序/导出）
        # base = ploneapi.portal.get().absolute_url()
        # href = u"{}/@@project_center_samples/{}".format(base, client_uid)

        projects = ploneapi.portal.get().restrictedTraverse('projects')
        base = projects.absolute_url()  # -> /TCRx/projects
        href = u"{}/@@project_center_samples/{}".format(base, client_uid)

        item.setdefault("replace", {})
        item["replace"]["ProjectTotal"] = u'<a href="{}">{}</a>'.format(href, total_projects)
        # item["replace"]["SampleTotal"] = u'<a href="{}">{}</a>'.format(href, sample_total)

        return item

    def _get_client_sample_brains(self, client_uid):
        if not hasattr(self, "_sample_brains_cache"):
            self._sample_brains_cache = {}

        if client_uid in self._sample_brains_cache:
            return self._sample_brains_cache[client_uid]

        cat = None
        try:
            cat = getToolByName(self.context, "senaite_catalog_sample")
        except Exception:
            pass
        if cat is None:
            cat = getToolByName(self.context, "portal_catalog")

        query = {"portal_type": ("AnalysisRequest", "Sample")}

        try:
            indexes = set(cat.indexes())
        except Exception:
            indexes = set()

        for idx in ("getClientUID", "ClientUID", "getClientUIDs"):
            if idx in indexes:
                query[idx] = client_uid
                break

        brains = cat.searchResults(**query)
        self._sample_brains_cache[client_uid] = brains
        return brains

    def _project_names_for_client(self, client_uid):
        names = set()
        for b in self._get_client_sample_brains(client_uid):
            # 先尝试从 brain 的元数据拿（若没建索引就从对象拿）
            val = getattr(b, "ProjectName", None)
            if not val:
                try:
                    o = b.getObject()
                except Exception:
                    o = None
                if o is not None:
                    # 兼容 getProjectName()/ProjectName 两种写法
                    getter = getattr(o, "getProjectName", None)
                    val = getter() if callable(getter) else getattr(o, "ProjectName", None)

            # 归一化/去重
            if val:
                if isinstance(val, (list, tuple, set)):
                    for v in val:
                        v = api.safe_unicode(v)
                        if v:
                            names.add(v)
                else:
                    val = api.safe_unicode(val)
                    if val:
                        names.add(val)

        return u", ".join(sorted(names)) or u""

    def _sample_total_for_client(self, client_uid):
        return len(self._get_client_sample_brains(client_uid))

    def _project_names_set_for_client(self, client_uid):
        names = set()
        for b in self._get_client_sample_brains(client_uid):
            val = getattr(b, "ProjectName", None)
            if not val:
                try:
                    o = b.getObject()
                except Exception:
                    o = None
                if o is not None:
                    getter = getattr(o, "getProjectName", None)
                    val = getter() if callable(getter) else getattr(o, "ProjectName", None)
            if val:
                if isinstance(val, (list, tuple, set)):
                    for v in val:
                        v = api.safe_unicode(v)
                        if v:
                            names.add(v)
                else:
                    val = api.safe_unicode(val)
                    if val:
                        names.add(val)
        return names

    def _project_names_for_client(self, client_uid):
        names = self._project_names_set_for_client(client_uid)
        return u", ".join(sorted(names)) if names else u""

    def _project_total_for_client(self, client_uid):
        return len(self._project_names_set_for_client(client_uid))


