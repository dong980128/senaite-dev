# -*- coding:utf-8 -*-
from bika.lims import api
from Products.Five.browser import BrowserView


class LabProcessAddView(BrowserView):
    """@@labprocess-add - create Labprocess without ++add++"""

    def __call__(self):
        req = self.request

        if req.get("form.submitted"):
            title = (req.get("title") or "").strip()
            process_key = (req.get("process_key") or "").strip()
            version = (req.get("version") or "").strip() or"v1.0"

            obj = api.create(self.context, "LabProcess", title=title)

            try:
                obj.process_key = process_key
                obj.version = version
            except Exception:
                pass

            return req.response.redirect(obj.absolute_url())
        return self.index()


