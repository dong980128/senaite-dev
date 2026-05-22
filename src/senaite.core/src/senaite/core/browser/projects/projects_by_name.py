# -*- coding: utf-8 -*-
from Products.Five import BrowserView
from Products.Five.browser.pagetemplatefile import ViewPageTemplateFile
from Products.CMFCore.utils import getToolByName
from bika.lims import api

try:
    from urllib import quote
except Exception:
    from urllib.parse import quote


class ProjectsByNameView(BrowserView):
    """按 ProjectName 聚合样本（不加索引，直接遍历对象）"""
    template = ViewPageTemplateFile("templates/projects_by_name.pt")

    def __call__(self):
        self._update()
        return self.template()

    def _update(self):
        portal = api.get_portal()
        catalog = getToolByName(portal, "portal_catalog")

        # 直接取所有 AnalysisRequest（先打通页面，后面再换索引优化）
        brains = catalog(portal_type="AnalysisRequest",
                         sort_on="created", sort_order="reverse")

        counter = {}
        for b in brains:
            try:
                obj = b.getObject()  # 无索引/无metadata时只能取对象
            except Exception:
                continue

            try:
                pname = obj.getProjectName()
            except Exception:
                pname = getattr(obj, "ProjectName", None)

            pname = api.safe_unicode(pname or u"").strip() or u"(未填)"
            counter[pname] = counter.get(pname, 0) + 1

        base_samples_url = portal.absolute_url().rstrip("/") + "/samples"

        # 排序：把“未填”放在最后
        def _key(x):
            try:
                return (x == u"(未填)", x.lower())
            except Exception:
                return (x == u"(未填)", x)

        rows = []
        for pname in sorted(counter.keys(), key=_key):
            # py2的quote需要bytes
            q = "project_name=" + quote(pname.encode("utf-8"))
            rows.append({
                "project": pname,
                "count": counter[pname],
                "link": base_samples_url + "?" + q,
            })

        self._rows = rows

    def get_rows(self):
        return getattr(self, "_rows", [])
