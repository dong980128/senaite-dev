# -*- coding: utf-8 -*-
#
# This file is part of SENAITE.CORE.
#
# SENAITE.CORE is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, version 2.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc., 51
# Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# Copyright 2018-2025 by it's authors.
# Some rights reserved, see README and LICENSE.

from bika.lims import api, logger
from bika.lims import senaiteMessageFactory as _
from plone.app.layout.viewlets import ViewletBase
from Products.Five.browser.pagetemplatefile import ViewPageTemplateFile
from Products.CMFCore.utils import getToolByName
from senaite.core.registry import get_registry_record


class LabAnalysesViewlet(ViewletBase):
    """Laboratory analyses section viewlet for Sample view
    """
    index = ViewPageTemplateFile("templates/sampleanalyses.pt")

    title = _("Analyses")
    icon_name = "analysisservice"
    capture = "lab"

    @property
    def sample(self):
        return self.context

    def is_collapsed(self):
        name = "sampleview_collapse_{}_analysis_table".format(
            self.capture)
        return get_registry_record(name, default=False)

    def available(self):
        """Returns true if this sample contains at least one analysis for the
        point of capture (capture)
        """
        if self._has_active_processrun_task_analyses():
            return False

        analyses = self.sample.getAnalyses(getPointOfCapture=self.capture)
        return len(analyses) > 0


    def get_listing_view(self):
        request = api.get_request()
        view_name = "table_{}_analyses".format(self.capture)
        view = api.get_view(view_name, context=self.sample, request=request)
        return view

    def contents_table(self):
        view = self.get_listing_view()
        view.update()
        view.before_render()
        return view.ajax_contents_table()

    def _has_active_processrun_task_analyses(self):
        """Active processrun exists AND any taskrun has analysis_uids"""
        try:
            ar = self.sample  # 在 viewlet 里通常 sample 就是 AR
        except Exception:
            ar = self.context

        try:
            pc = api.get_tool("portal_catalog")
        except Exception:
            pc = getToolByName(ar, "portal_catalog")

        ar_path = "/".join(ar.getPhysicalPath())

        # 1) 找 AR 下的 LabProcessRun
        runs = pc(portal_type="LabProcessRun", path={"query": ar_path, "depth": 2})
        if not runs:
            return False

        for b in runs:
            try:
                run = b.getObject()
                status = getattr(run, "status", "") or ""
                if status.strip().lower() != "active":
                    continue

                # 2) 找这个 run 下的 LabTaskRun
                run_path = "/".join(run.getPhysicalPath())
                trs = pc(portal_type="LabTaskRun", path={"query": run_path, "depth": 2})
                for tb in trs:
                    tr = tb.getObject()
                    uids = getattr(tr, "analysis_uids", None) or []
                    if uids:
                        return True
            except Exception:
                continue

        return False


class FieldAnalysesViewlet(LabAnalysesViewlet):
    """Field analyses section viewlet for Sample view
    """
    title = _("Field Analyses")
    capture = "field"


class QCAnalysesViewlet(LabAnalysesViewlet):
    """QC analyses section viewlet for Sample view
    """
    title = _("QC Analyses")
    capture = "qc"

    def available(self):
        """Returns true if this sample contains at least one qc analysis
        """
        analyses = self.sample.getQCAnalyses()
        return len(analyses) > 0
