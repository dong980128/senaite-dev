# -*- coding: utf-8 -*-
from Products.Five import BrowserView
from Products.Five.browser.pagetemplatefile import ViewPageTemplateFile
from bika.lims import logger
from bika.lims.browser.analysisrequest.tables import LabAnalysesTable

class LabAnalysesTableByService(LabAnalysesTable):
    """Renders LabAnalysesTable filtered by Service (UID or Title)."""
    # 如果 .pt 与本 .py 同目录，用下面这行；若在 templates/ 目录，请改成 "templates/lab_analyses_by_service.pt"
    index = ViewPageTemplateFile("templates/sampleanalyses.pt")

    def __call__(self, service_uid=None, service_title=None, **kw):
        # 允许通过 kwargs 传参，并同步注入 request
        if service_uid:
            self.request.set('service_uid', service_uid)
        if service_title:
            self.request.set('service_title', service_title)
        for k, v in kw.items():
            self.request.set(k, v)

        logger.info(
            u"[@@lab_analyses_by_service] service_uid=%s, service_title=%s",
            service_uid, service_title
        )
        return self.index()

    def table(self):
        tbl = LabAnalysesTableByService(self.context, self.request)
        tbl.update()
        return tbl
