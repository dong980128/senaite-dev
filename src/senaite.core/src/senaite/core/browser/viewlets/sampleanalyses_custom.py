# -*- coding: utf-8 -*-
from bika.lims import api
from .sampleanalyses import LabAnalysesViewlet as BaseLab

class LabAnalysesViewletCustom(BaseLab):
    def get_listing_view(self):
        request = api.get_request()
        return api.get_view("table_lab_analyses_custom",
                            context=self.sample, request=request)

    def ajax_contents_table(self):
        view = self.get_listing_view()
        return view()

    def contents_table(self):
        view = self.get_listing_view()
        return view()
