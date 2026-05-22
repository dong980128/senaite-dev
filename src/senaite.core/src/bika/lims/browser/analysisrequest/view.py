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

from bika.lims import api
from Products.CMFCore.utils import getToolByName
from zExceptions import Unauthorized
from bika.lims.browser import BrowserView
from Products.Five.browser.pagetemplatefile import ViewPageTemplateFile
from AccessControl import getSecurityManager
from bika.lims import logger

class AnalysisRequestViewView(BrowserView):
    """Main AR View
    """
    template = ViewPageTemplateFile("templates/analysisrequest_view.pt")

    def __init__(self, context, request):
        self.init__ = super(
            AnalysisRequestViewView, self).__init__(context, request)
        self.icon = "{}/{}".format(
            self.portal_url,
            "/++resource++bika.lims.images/sample_big.png",
        )

    def __call__(self):
        context = self.context
        review_state = api.get_workflow_status_of(context)

        required_permission = 'senaite.core: Transition: Receive Sample'
        mtool = getToolByName(context, 'portal_membership')
        has_permission = mtool.checkPermission(required_permission, context)
        member = mtool.getAuthenticatedMember()
        user_roles = member.getRoles()
        username = member.getUserName()

        is_labclerk = "LabClerk" in user_roles
        is_manager = "Manager" in user_roles
        is_sampler = "Sampler" in user_roles

        if review_state == "sample_due":
            if not (is_labclerk or is_manager or is_sampler):
                raise Unauthorized("样本尚未接收，禁止访问详情页。")

        return self.template()

    def is_hazardous(self):
        """Checks if the AR is hazardous
        """
        return self.context.getHazardous()

    def is_retest(self):
        """Checks if the AR is a retest
        """
        return self.context.getRetest()

    def exclude_invoice(self):
        """True if the invoice should be excluded
        """
        return self.context.getInvoiceExclude()

    def show_categories(self):
        """Check the setup if analysis services should be categorized
        """
        setup = api.get_setup()
        return setup.getCategoriseAnalysisServices()

    def Analyses(self):
        """只返回 assigned to 是当前用户的分析项目"""
        current_user = getSecurityManager().getUser().getId()

        analyses = self.context.getAnalyses()

        return [
            a for a in analyses
            if a.getAnalyses() and a.getAnalyses() == current_user
        ]
