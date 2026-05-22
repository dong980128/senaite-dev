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

import collections
import datetime
import json
from calendar import monthrange
from operator import itemgetter
from time import time

import logging

LOG = logging.getLogger("dashboard.grouped")

from bika.lims import bikaMessageFactory as _
# from bika.lims import logger
from bika.lims.api import get_current_client
from bika.lims.api import get_tool
from bika.lims.api import get_url
from bika.lims.api import search
from bika.lims.browser import BrowserView
from bika.lims.utils import get_strings
from bika.lims.utils import get_unicode
from DateTime import DateTime
from plone import api
from plone import protect
from plone.memoize import ram
from plone.memoize import view as viewcache
from Products.Archetypes.public import DisplayList
from Products.CMFCore.utils import getToolByName
from Products.Five.browser.pagetemplatefile import ViewPageTemplateFile
from senaite.core.catalog import ANALYSIS_CATALOG
from senaite.core.catalog import SAMPLE_CATALOG
from senaite.core.catalog import WORKSHEET_CATALOG
from Products.CMFPlone.utils import safe_unicode
from plone.memoize import view as memoize

DASHBOARD_FILTER_COOKIE = 'dashboard_filter_cookie'

# Supported periodicities for evolution charts
PERIODICITY_DAILY = "d"
PERIODICITY_WEEKLY = "w"
PERIODICITY_MONTHLY = "m"
PERIODICITY_QUARTERLY = "q"
PERIODICITY_BIANNUAL = "b"
PERIODICITY_YEARLY = "y"
PERIODICITY_ALL = "a"

AR_STATE_CARDS = [
    ("sample_due", u"待接收"),
    ("to_be_verified", u"待复核"),
    ("verified", u"已复核"),
    ("published", u"已发布"),
    ("sample_received", u"等待结果"),
]


def get_dashboard_registry_record():
    """
    Return the 'senaite.core.dashboard_panels_visibility' values.
    :return: A dictionary or None
    """

    registry = api.portal.get_registry_record(
        'senaite.core.dashboard_panels_visibility')
    return registry

    return dict()


def set_dashboard_registry_record(registry_info):
    """
    Sets the 'senaite.core.dashboard_panels_visibility' values.

    :param registry_info: A dictionary type object with all its values as
    *unicode* objects.
    :return: A dictionary or None
    """
    api.portal.set_registry_record(
        'senaite.core.dashboard_panels_visibility', registry_info)


def setup_dashboard_panels_visibility_registry(section_name):
    """
    Initializes the values for panels visibility in registry_records. By
    default, only users with LabManager or Manager roles can see the panels.
    :param section_name:
    :return: An string like: "role1,yes,role2,no,rol3,no"
    """

    registry_info = get_dashboard_registry_record()
    role_permissions_list = []
    # Getting roles defined in the system
    roles = []
    acl_users = get_tool("acl_users")
    roles_tree = acl_users.portal_role_manager.listRoleIds()
    for role in roles_tree:
        roles.append(role)
    # Set view permissions to each role as 'yes':
    # "role1,yes,role2,no,rol3,no"
    for role in roles:
        role_permissions_list.append(role)
        visible = 'no'
        if role in ['LabManager', 'Manager']:
            visible = 'yes'
        role_permissions_list.append(visible)
    role_permissions = ','.join(role_permissions_list)

    # Set permissions string into dict
    registry_info[get_unicode(section_name)] = get_unicode(role_permissions)
    # Set new valugies to registry record
    set_dashboard_registry_record(registry_info)
    return registry_info


def get_dashboard_panels_visibility_by_section(section_name):
    """
    Return a list of pairs as values that represents the role-permission
    view relation for the panel section passed in.
    :param section_name: the panels section id.
    :return: a list of tuples.
    """
    registry_info = get_dashboard_registry_record()
    if section_name not in registry_info:
        # Registry hasn't been set, do it at least for this section
        registry_info = \
            setup_dashboard_panels_visibility_registry(section_name)

    pairs = registry_info.get(section_name)
    pairs = get_strings(pairs)
    if pairs is None:
        # In the registry, but with None value?
        setup_dashboard_panels_visibility_registry(section_name)
        return get_dashboard_panels_visibility_by_section(section_name)

    pairs = pairs.split(',')
    if len(pairs) == 0 or len(pairs) % 2 != 0:
        # Non-valid or malformed value
        setup_dashboard_panels_visibility_registry(section_name)
        return get_dashboard_panels_visibility_by_section(section_name)

    result = [
        (pairs[i], pairs[i + 1]) for i in range(len(pairs)) if i % 2 == 0]
    return result


def is_panel_visible_for_user(panel, user):
    """
    Checks if the user is allowed to see the panel
    :param panel: panel ID as string
    :param user: a MemberData object
    :return: Boolean
    """
    roles = user.getRoles()
    visibility = get_dashboard_panels_visibility_by_section(panel)
    for pair in visibility:
        if pair[0] in roles and pair[1] == 'yes':
            return True
    return False


class DashboardView(BrowserView):
    template = ViewPageTemplateFile("templates/dashboard.pt")

    def _dbg(self, msg, **kw):

        try:
            uid = getattr(self.request, 'ACTUAL_URL', '') or getattr(self.request, 'URL', '')
        except Exception:
            uid = ''
        if kw:
            msg = "{} | {}".format(msg, " ".join(["{}={}".format(k, kw[k]) for k in sorted(kw)]))
        LOG.info("[AR-GROUP] %s", msg)

    def __init__(self, context, request):
        BrowserView.__init__(self, context, request)
        self.dashboard_cookie = None
        self.member = None

    def __call__(self):
        # If a client contact, redirect to the client's page
        client = get_current_client()
        if client:
            url = get_url(client)
            return self.request.response.redirect(url)

        frontpage_url = self.portal_url + "/senaite-frontpage"
        if not self.context.bika_setup.getDashboardByDefault():
            # Do not render dashboard, render frontpage instead
            self.request.response.redirect(frontpage_url)
            return

        mtool = getToolByName(self.context, 'portal_membership')
        if mtool.isAnonymousUser():
            # Anonymous user, redirect to frontpage
            self.request.response.redirect(frontpage_url)
            return

        self.member = mtool.getAuthenticatedMember()
        self.periodicity = self.request.get('p', PERIODICITY_WEEKLY)
        self.dashboard_cookie = self.check_dashboard_cookie()
        date_range = self.get_date_range(self.periodicity)
        self.date_from = date_range[0]
        self.date_to = date_range[1]

        return self.template()

    def check_dashboard_cookie(self):
        """
        Check if the dashboard cookie should exist through bikasetup
        configuration.

        If it should exist but doesn't exist yet, the function creates it
        with all values as default.
        If it should exist and already exists, it returns the value.
        Otherwise, the function returns None.

        :return: a dictionary of strings
        """
        # Getting cookie
        cookie_raw = self.request.get(DASHBOARD_FILTER_COOKIE, None)
        # If it doesn't exist, create it with default values
        if cookie_raw is None:
            cookie_raw = self._create_raw_data()
            self.request.response.setCookie(
                DASHBOARD_FILTER_COOKIE,
                json.dumps(cookie_raw),
                quoted=False,
                path='/')
            return cookie_raw
        return get_strings(json.loads(cookie_raw))

    def is_filter_selected(self, selection_id, value):
        """
        Compares whether the 'selection_id' parameter value saved in the
        cookie is the same value as the "value" parameter.

        :param selection_id: a string as a dashboard_cookie key.
        :param value: The value to compare against the value from
        dashboard_cookie key.
        :return: Boolean.
        """
        selected = self.dashboard_cookie.get(selection_id)
        return selected == value

    def is_admin_user(self):
        """
        Checks if the user is the admin or a SiteAdmin user.
        :return: Boolean
        """
        user = api.user.get_current()
        roles = user.getRoles()
        return "LabManager" in roles or "Manager" in roles

    def _create_raw_data(self):
        """
        Gathers the different sections ids and creates a string as first
        cookie data.

        :return: A dictionary like:
            {'analyses':'all','analysisrequest':'all','worksheets':'all'}
        """
        result = {}
        for section in self.get_sections():
            result[section.get('id')] = 'all'
        return result

    def get_date_range(self, periodicity=PERIODICITY_WEEKLY):
        """Returns a date range (date from, date to) that suits with the passed
        in periodicity.

        :param periodicity: string that represents the periodicity
        :type periodicity: str
        :return: A date range
        :rtype: [(DateTime, DateTime)]
        """

        today = datetime.date.today()
        if periodicity == PERIODICITY_DAILY:
            # Daily, load last 30 days
            date_from = DateTime() - 30
            date_to = DateTime() + 1
            return date_from, date_to

        if periodicity == PERIODICITY_MONTHLY:
            # Monthly, load last 2 years
            min_year = today.year - 1 if today.month == 12 else today.year - 2
            min_month = 1 if today.month == 12 else today.month
            date_from = DateTime(min_year, min_month, 1)
            date_to = DateTime(today.year, today.month,
                               monthrange(today.year, today.month)[1],
                               23, 59, 59)
            return date_from, date_to

        if periodicity == PERIODICITY_QUARTERLY:
            # Quarterly, load last 4 years
            m = (((today.month - 1) / 3) * 3) + 1
            min_year = today.year - 4 if today.month == 12 else today.year - 5
            date_from = DateTime(min_year, m, 1)
            date_to = DateTime(today.year, m + 2,
                               monthrange(today.year, m + 2)[1], 23, 59,
                               59)
            return date_from, date_to
        if periodicity == PERIODICITY_BIANNUAL:
            # Biannual, load last 10 years
            m = (((today.month - 1) / 6) * 6) + 1
            min_year = today.year - 10 if today.month == 12 else today.year - 11
            date_from = DateTime(min_year, m, 1)
            date_to = DateTime(today.year, m + 5,
                               monthrange(today.year, m + 5)[1], 23, 59,
                               59)
            return date_from, date_to

        if periodicity in [PERIODICITY_YEARLY, PERIODICITY_ALL]:
            # Yearly or All time, load last 15 years
            min_year = today.year - 15 if today.month == 12 else today.year - 16
            date_from = DateTime(min_year, 1, 1)
            date_to = DateTime(today.year, 12, 31, 23, 59, 59)
            return date_from, date_to

        # Default Weekly, load last six months
        year, weeknum, dow = today.isocalendar()
        min_year = today.year if today.month > 6 else today.year - 1
        min_month = today.month - 6 if today.month > 6 \
            else (today.month - 6) + 12
        date_from = DateTime(min_year, min_month, 1)
        date_to = DateTime() - dow + 7
        return date_from, date_to

    def get_sections(self):
        """ Returns an array with the sections to be displayed.
            Every section is a dictionary with the following structure:
                {'id': <section_identifier>,
                 'title': <section_title>,
                'panels': <array of panels>}
        """
        sections = []
        user = api.user.get_current()
        # if is_panel_visible_for_user('analyses', user):
        #     sections.append(self.get_analyses_section())
        if is_panel_visible_for_user("sampletypes", user):
            sections.append(self.get_sampletypes_section())

        if is_panel_visible_for_user('analysisrequests', user):
            sections.append(self.get_analysisrequests_section())
        # if is_panel_visible_for_user('worksheets', user):
        #     sections.append(self.get_worksheets_section())

        return sections

    def get_filter_options(self):
        """
        Returns dasboard filter options.
        :return: Boolean
        """
        dash_opt = DisplayList((
            ('all', _('All')),
            ('mine', _('Mine')),
        ))
        return dash_opt

    def _getStatistics(self, name, description, url, catalog, criterias, total):
        out = {'type': 'simple-panel',
               'name': name,
               'class': 'informative',
               'description': description,
               'total': total,
               'link': self.portal_url + '/' + url}

        results = 0
        ratio = 0
        if total > 0:
            results = self.search_count(criterias, catalog.id)
            results = results if total >= results else total
            ratio = (float(results) / float(total)) * 100 if results > 0 else 0
        ratio = str("%%.%sf" % 1) % ratio
        out['legend'] = _('of') + " " + str(total) + ' (' + ratio + '%)'
        out['number'] = results
        out['percentage'] = float(ratio)
        return out

    def get_analysisrequests_section(self):
        """ Returns the section dictionary related with Analysis
            Requests, that contains some informative panels (like
            ARs to be verified, ARs to be published, etc.)
        """
        out = []
        catalog = getToolByName(self.context, SAMPLE_CATALOG)
        query = {'portal_type': "AnalysisRequest",
                 'is_active': True}

        st_uid = self.request.get('st', '').strip()
        if st_uid:
            query['getSampleTypeUID'] = st_uid

        # Check if dashboard_cookie contains any values to query
        # elements by
        query = self._update_criteria_with_filters(query, 'analysisrequests')

        # Active Samples (All)
        total = self.search_count(query, catalog.id)

        if self.has_dashboard_role('Publisher'):
            only_states = [
                ('to_be_verified', _('Samples to be verified'), _("To be verified")),
                ('verified', _('Samples verified'), _("Verified")),
                ('published', _('Samples published'), _("Published")),
            ]
            for state, name, desc in only_states:
                # purl = 'samples?samples_review_state={}'.format(state)
                purl = self._samples_path(samples_review_state=state, getSampleTypeUID=st_uid)
                query['review_state'] = [state]
                out.append(self._getStatistics(name, desc, purl, catalog, query, total))

            outevo = self.fill_dates_evo(catalog, query)
            out.append({
                'type': 'bar-chart-panel',
                'name': _('Evolution of Samples'),
                'class': 'informative',
                'description': _('Evolution of Samples'),
                'data': json.dumps(outevo),
                'datacolors': json.dumps(self.get_colors_palette())
            })

            # section 标题：若带有st(样本名)
            section_title = _('Samples')
            if st_uid:
                # 复用已有的标题映射（或用 _sampletype_title_by_uid）
                title = self._sampletype_title_by_uid(st_uid)
                section_title = u'样本（{}）'.format(title)

            return {'id': 'analysisrequests', 'title': section_title, 'panels': out}

        # Sampling workflow enabled?
        if self.context.bika_setup.getSamplingWorkflowEnabled():
            # Samples awaiting to be sampled or scheduled
            name = _('Samples to be sampled')
            desc = _("To be sampled")

            # purl = 'samples?samples_review_state=to_be_sampled'
            purl = self._samples_path(samples_review_state='to_be_sampled',
                                      getSampleTypeUID=st_uid)
            query['review_state'] = ['to_be_sampled', ]
            out.append(self._getStatistics(name, desc, purl, catalog, query, total))

            # Samples awaiting to be preserved
            name = _('Samples to be preserved')
            desc = _("To be preserved")
            # purl = 'samples?samples_review_state=to_be_preserved'
            purl = self._samples_path(samples_review_state='to_be_preserved',
                                      getSampleTypeUID=st_uid)
            query['review_state'] = ['to_be_preserved', ]
            out.append(self._getStatistics(name, desc, purl, catalog, query, total))

            # Samples scheduled for Sampling
            name = _('Samples scheduled for sampling')
            desc = _("Sampling scheduled")
            # purl = 'samples?samples_review_state=scheduled_sampling'
            purl = self._samples_path(samples_review_state='scheduled_sampling',
                                      getSampleTypeUID=st_uid)
            query['review_state'] = ['scheduled_sampling', ]
            out.append(self._getStatistics(name, desc, purl, catalog, query, total))

        # Samples awaiting for reception
        name = _('Samples to be received')
        desc = _("Reception pending")
        # purl = 'samples?samples_review_state=sample_due'
        purl = self._samples_path(samples_review_state='sample_due',
                                  getSampleTypeUID=st_uid)
        query['review_state'] = ['sample_due', ]
        out.append(self._getStatistics(name, desc, purl, catalog, query, total))

        # Samples under way
        name = _('Samples with results pending')
        desc = _("Results pending")
        # purl = 'samples?samples_review_state=sample_received'
        purl = self._samples_path(samples_review_state='sample_received',
                                  getSampleTypeUID=st_uid)
        query['review_state'] = ['sample_received', ]
        out.append(self._getStatistics(name, desc, purl, catalog, query, total))

        # Samples to be verified
        name = _('Samples to be verified')
        desc = _("To be verified")
        # purl = 'samples?samples_review_state=to_be_verified'
        purl = self._samples_path(samples_review_state='to_be_verified',
                                  getSampleTypeUID=st_uid)
        query['review_state'] = ['to_be_verified', ]
        out.append(self._getStatistics(name, desc, purl, catalog, query, total))

        # Samples verified (to be published)
        name = _('Samples verified')
        desc = _("Verified")
        # purl = 'samples?samples_review_state=verified'
        purl = self._samples_path(samples_review_state='verified',
                                  getSampleTypeUID=st_uid)
        query['review_state'] = ['verified', ]
        out.append(self._getStatistics(name, desc, purl, catalog, query, total))

        # Samples published
        name = _('Samples published')
        desc = _("Published")
        # purl = 'samples?samples_review_state=published'
        purl = self._samples_path(
            samples_review_state='published',
            **({'getSampleTypeUID': st_uid} if st_uid else {})
        )
        query['review_state'] = ['published', ]
        out.append(self._getStatistics(name, desc, purl, catalog, query, total))

        # Samples to be printed
        if self.context.bika_setup.getPrintingWorkflowEnabled():
            name = _('Samples to be printed')
            desc = _("To be printed")
            purl = 'samples?samples_getPrinted=0'
            query['getPrinted'] = '0'
            query['review_state'] = ['published', ]
            out.append(
                self._getStatistics(name, desc, purl, catalog, query, total))

        # Chart with the evolution of ARs over a period, grouped by
        # periodicity
        outevo = self.fill_dates_evo(catalog, query)
        out.append({'type': 'bar-chart-panel',
                    'name': _('Evolution of Samples'),
                    'class': 'informative',
                    'description': _('Evolution of Samples'),
                    'data': json.dumps(outevo),
                    'datacolors': json.dumps(self.get_colors_palette())})

        return {'id': 'analysisrequests',
                'title': _('Samples'),
                'panels': out}

    def get_worksheets_section(self):
        """ Returns the section dictionary related with Worksheets,
            that contains some informative panels (like
            WS to be verified, WS with results pending, etc.)
        """
        out = []
        bc = getToolByName(self.context, WORKSHEET_CATALOG)
        query = {'portal_type': "Worksheet", }

        # Check if dashboard_cookie contains any values to query
        # elements by
        query = self._update_criteria_with_filters(query, 'worksheets')

        # Active Worksheets (all)
        total = self.search_count(query, bc.id)
        if self.has_dashboard_role('Publisher'):
            items = [
                ('to_be_verified', _('To be verified'), 'worksheets?list_review_state=to_be_verified'),
                ('verified', _('Verified'), 'worksheets?list_review_state=verified'),
            ]
            for state, label, purl in items:
                name = label
                desc = label
                query['review_state'] = [state]
                out.append(self._getStatistics(name, desc, purl, bc, query, total))

            outevo = self.fill_dates_evo(bc, query)
            out.append({
                'type': 'bar-chart-panel',
                'name': _('Evolution of Worksheets'),
                'class': 'informative',
                'description': _('Evolution of Worksheets'),
                'data': json.dumps(outevo),
                'datacolors': json.dumps(self.get_colors_palette()),
            })
            return {'id': 'worksheets', 'title': _('Worksheets'), 'panels': out}
        # Open worksheets
        name = _('Results pending')
        desc = _('Results pending')
        purl = 'worksheets?list_review_state=open'
        query['review_state'] = ['open']
        out.append(self._getStatistics(name, desc, purl, bc, query, total))

        # Worksheets to be verified
        name = _('To be verified')
        desc = _('To be verified')
        purl = 'worksheets?list_review_state=to_be_verified'
        query['review_state'] = ['to_be_verified', ]
        out.append(self._getStatistics(name, desc, purl, bc, query, total))

        # Worksheets verified
        name = _('Verified')
        desc = _('Verified')
        purl = 'worksheets?list_review_state=verified'
        query['review_state'] = ['verified', ]
        out.append(self._getStatistics(name, desc, purl, bc, query, total))

        # Chart with the evolution of WSs over a period, grouped by
        # periodicity
        outevo = self.fill_dates_evo(bc, query)
        out.append({'type': 'bar-chart-panel',
                    'name': _('Evolution of Worksheets'),
                    'class': 'informative',
                    'description': _('Evolution of Worksheets'),
                    'data': json.dumps(outevo),
                    'datacolors': json.dumps(self.get_colors_palette())})

        return {'id': 'worksheets',
                'title': _('Worksheets'),
                'panels': out}

    def get_analyses_section(self):
        """ Returns the section dictionary related with Analyses,
            that contains some informative panels (analyses pending
            analyses assigned, etc.)
        """
        out = []
        bc = getToolByName(self.context, ANALYSIS_CATALOG)
        query = {'portal_type': "Analysis", 'is_active': True}

        # Check if dashboard_cookie contains any values to query elements by
        query = self._update_criteria_with_filters(query, 'analyses')

        # Active Analyses (All)
        total = self.search_count(query, bc.id)

        if self.has_dashboard_role('Publisher'):
            for state, label in (('to_be_verified', _('To be verified')),
                                 ('verified', _('Verified'))):
                name = label
                desc = label
                purl = '#'  # Analyses 这里原本就是 '#'
                query['review_state'] = [state]
                out.append(self._getStatistics(name, desc, purl, bc, query, total))

            # 趋势图保留
            outevo = self.fill_dates_evo(bc, query)
            out.append({
                'type': 'bar-chart-panel',
                'name': _('Evolution of Analyses'),
                'class': 'informative',
                'description': _('Evolution of Analyses'),
                'data': json.dumps(outevo),
                'datacolors': json.dumps(self.get_colors_palette()),
            })
            return {'id': 'analyses', 'title': _('Analyses'), 'panels': out}

        # Analyses to be assigned
        name = _('Assignment pending')
        desc = _('Assignment pending')
        purl = '#'
        query['review_state'] = ['unassigned']
        out.append(self._getStatistics(name, desc, purl, bc, query, total))

        # Analyses pending
        name = _('Results pending')
        desc = _('Results pending')
        purl = '#'
        query['review_state'] = ['unassigned', 'assigned', ]
        out.append(self._getStatistics(name, desc, purl, bc, query, total))

        # Analyses to be verified
        name = _('To be verified')
        desc = _('To be verified')
        purl = '#'
        query['review_state'] = ['to_be_verified', ]
        out.append(self._getStatistics(name, desc, purl, bc, query, total))

        # Analyses verified
        name = _('Verified')
        desc = _('Verified')
        purl = '#'
        query['review_state'] = ['verified', ]
        out.append(self._getStatistics(name, desc, purl, bc, query, total))

        # Chart with the evolution of Analyses over a period, grouped by
        # periodicity
        outevo = self.fill_dates_evo(bc, query)
        out.append({'type': 'bar-chart-panel',
                    'name': _('Evolution of Analyses'),
                    'class': 'informative',
                    'description': _('Evolution of Analyses'),
                    'data': json.dumps(outevo),
                    'datacolors': json.dumps(self.get_colors_palette())})
        return {'id': 'analyses',
                'title': _('Analyses'),
                'panels': out}

    def get_states_map(self, portal_type):
        if portal_type == 'Analysis':
            return {'unassigned': _('Assignment pending'),
                    'assigned': _('Results pending'),
                    'to_be_verified': _('To be verified'),
                    'rejected': _('Rejected'),
                    'retracted': _('Retracted'),
                    'verified': _('Verified'),
                    'published': _('Published')}
        elif portal_type == 'AnalysisRequest':
            return {'to_be_sampled': _('To be sampled'),
                    'to_be_preserved': _('To be preserved'),
                    'scheduled_sampling': _('Sampling scheduled'),
                    'sample_due': _('Reception pending'),
                    'rejected': _('Rejected'),
                    'invalid': _('Invalid'),
                    'sample_received': _('Results pending'),
                    'assigned': _('Results pending'),
                    'to_be_verified': _('To be verified'),
                    'verified': _('Verified'),
                    'published': _('Published')}
        elif portal_type == 'Worksheet':
            return {'open': _('Results pending'),
                    'to_be_verified': _('To be verified'),
                    'verified': _('Verified')}

    def get_colors_palette(self):
        return {
            'to_be_sampled': '#917A4C',
            _('To be sampled'): '#917A4C',

            'to_be_preserved': '#C2803E',
            _('To be preserved'): '#C2803E',

            'scheduled_sampling': '#F38630',
            _('Sampling scheduled'): '#F38630',

            'sample_due': '#ffff8d',
            _('Reception pending'): '#ffff8d',

            'sample_received': '#a1887f',
            _('Assignment pending'): '#a1887f',
            _('Sample received'): '#a1887f',

            'assigned': '#ddd',
            'open': '#ddd',
            _('Results pending'): '#ddd',

            'rejected': '#abc',
            'retracted': '#abc',
            _('Rejected'): '#abc',
            _('Retracted'): '#abc',

            'invalid': '#e65100',
            _('Invalid'): '#e65100',

            'to_be_verified': '#18ffff',
            _('To be verified'): '#18ffff',

            'verified': '#0091ea',
            _('Verified'): '#0091ea',

            'published': '#00c853',
            _('Published'): '#00c853',
        }

    def _getDateStr(self, period, created):
        if period == PERIODICITY_YEARLY:
            created = created.year()
        elif period == PERIODICITY_BIANNUAL:
            m = (((created.month() - 1) / 6) * 6) + 1
            created = '%s-%s' % (str(created.year())[2:], str(m).zfill(2))
        elif period == PERIODICITY_QUARTERLY:
            m = (((created.month() - 1) / 3) * 3) + 1
            created = '%s-%s' % (str(created.year())[2:], str(m).zfill(2))
        elif period == PERIODICITY_MONTHLY:
            created = '%s-%s' % (str(created.year())[2:], str(created.month()).zfill(2))
        elif period == PERIODICITY_WEEKLY:
            year, weeknum, dow = created.asdatetime().isocalendar()
            created = created - dow
            created = '%s-%s-%s' % (str(created.year())[2:], str(created.month()).zfill(2), str(created.day()).zfill(2))
        elif period == PERIODICITY_ALL:
            # All time, but evolution chart grouped by year
            created = created.year()
        else:
            created = '%s-%s-%s' % (str(created.year())[2:], str(created.month()).zfill(2), str(created.day()).zfill(2))
        return created

    def fill_dates_evo(self, catalog, query):
        sorted_query = collections.OrderedDict(sorted(query.items()))
        query_json = json.dumps(sorted_query)
        return self._fill_dates_evo(query_json, catalog.id, self.periodicity)

    def _fill_dates_evo_cachekey(method, self, query_json, catalog_name,
                                 periodicity):
        hour = time() // (60 * 60 * 2)
        return hour, catalog_name, query_json, periodicity

    @ram.cache(_fill_dates_evo_cachekey)
    def _fill_dates_evo(self, query_json, catalog_name, periodicity):
        """Returns an array of dictionaries, where each dictionary contains the
        amount of items created at a given date and grouped by review_state,
        based on the passed in periodicity.

        This is an expensive function that will not be called more than once
        every 2 hours (note cache decorator with `time() // (60 * 60 * 2)
        """
        outevoidx = {}
        outevo = []
        days = 1
        if periodicity == PERIODICITY_YEARLY:
            days = 336
        elif periodicity == PERIODICITY_BIANNUAL:
            days = 168
        elif periodicity == PERIODICITY_QUARTERLY:
            days = 84
        elif periodicity == PERIODICITY_MONTHLY:
            days = 28
        elif periodicity == PERIODICITY_WEEKLY:
            days = 7
        elif periodicity == PERIODICITY_ALL:
            days = 336

        # Get the date range
        date_from, date_to = self.get_date_range(periodicity)
        query = json.loads(query_json)
        if 'review_state' in query:
            del query['review_state']
        query['sort_on'] = 'created'
        query['created'] = {'query': (date_from, date_to),
                            'range': 'min:max'}

        otherstate = _('Other status')
        statesmap = self.get_states_map(query['portal_type'])
        stats = statesmap.values()
        stats.sort()
        stats.append(otherstate)
        statscount = {s: 0 for s in stats}
        # Add first all periods, cause we want all segments to be displayed
        curr = date_from.asdatetime()
        end = date_to.asdatetime()
        while curr < end:
            currstr = self._getDateStr(periodicity, DateTime(curr))
            if currstr not in outevoidx:
                outdict = {'date': currstr}
                for k in stats:
                    outdict[k] = 0
                outevo.append(outdict)
                outevoidx[currstr] = len(outevo) - 1
            curr = curr + datetime.timedelta(days=days)

        brains = search(query, catalog_name)
        for brain in brains:
            created = brain.created
            state = brain.review_state
            state = statesmap[state] if state in statesmap else otherstate
            created = self._getDateStr(periodicity, created)
            statscount[state] += 1
            if created in outevoidx:
                oidx = outevoidx[created]
                if state in outevo[oidx]:
                    outevo[oidx][state] += 1
                else:
                    outevo[oidx][state] = 1
            else:
                # Create new row
                currow = {'date': created,
                          state: 1}
                outevo.append(currow)

        # Remove all those states for which there is no data
        rstates = [k for k, v in statscount.items() if v == 0]
        for o in outevo:
            for r in rstates:
                if r in o:
                    del o[r]

        # Sort available status by number of occurences descending
        sorted_states = sorted(statscount.items(), key=itemgetter(1))
        sorted_states = map(lambda item: item[0], sorted_states)
        sorted_states.reverse()
        return {'data': outevo, 'states': sorted_states}

    def search_count(self, query, catalog_name):
        sorted_query = collections.OrderedDict(sorted(query.items()))
        query_json = json.dumps(sorted_query)
        return self._search_count(query_json, catalog_name)

    @viewcache.memoize
    def _search_count(self, query_json, catalog_name):
        query = json.loads(query_json)
        brains = search(query, catalog_name)
        return len(brains)

    def _update_criteria_with_filters(self, query, section_name):
        """
        This method updates the 'query' dictionary with the criteria stored in
        dashboard cookie.

        :param query: A dictionary with search criteria.
        :param section_name: The dashboard section name
        :return: The 'query' dictionary
        """
        if self.dashboard_cookie is None:
            return query
        cookie_criteria = self.dashboard_cookie.get(section_name)
        if cookie_criteria == 'mine':
            query['Creator'] = self.member.getId()
        return query

    def get_dashboard_panels_visibility(self, section_name):
        """
        Return a list of pairs as values that represents the role-permission
        view relation for the panel section.
        :param section_name: the panels section id.
        :return: a list of tuples.
        """

        return get_dashboard_panels_visibility_by_section(section_name)

    def has_dashboard_role(self, *role_names):
        """Return True if current user has any of the given roles."""
        user = api.user.get_current()
        roles = set(user.getRoles())
        return any(r in roles for r in role_names)

    # 样本根据类型分类
    def _setup_catalog(self):
        """setup 目录专用 catalog：senaite_catalog_setup"""
        return api.portal.get_tool('senaite_catalog_setup')

    @memoize.memoize
    def _sampletype_title_url_map(self):
        """一次性把 /setup/sampletypes 下所有样本类型做成 {UID: (Title, URL)} 映射"""
        portal = api.portal.get()
        setup_folder = portal.get('setup', None)
        if not setup_folder:
            return {}

        stypes = setup_folder.get('sampletypes', None)
        if not stypes:
            return {}

        root_path = '/'.join(stypes.getPhysicalPath())
        cat = self._setup_catalog()

        # 只拉 SampleType；用 brain.getObject() 拿 Title()，避免乱码
        brains = cat.unrestrictedSearchResults(
            portal_type='SampleType',
            path={'query': root_path, 'depth': 1},
        )

        mapping = {}
        for b in brains:
            try:
                obj = b.getObject()
            except Exception:
                obj = None

            # 优先对象 Title()；其次是 brain.Title（再做一次 safe_unicode）
            if obj is not None:
                title = obj.Title()
                url = obj.absolute_url()
            else:
                title = safe_unicode(getattr(b, 'Title', b.UID))
                url = b.getURL()

            mapping[b.UID] = (title, url)

        return mapping

    def _sample_catalog(self):
        """优先使用 SENAITE 的样本 catalog"""
        for cid in (
                "senaite_catalog_sample",
                "bika_catalog_sample",
                "senaite_catalog",
                "bika_catalog",
                "portal_catalog",
        ):
            try:
                return api.portal.get_tool(cid)
            except Exception:
                continue
        return api.portal.get_tool("portal_catalog")

    def _sample_roots(self):
        """返回查询根路径（/clients 和 /samples），都不存在则 None=全站"""
        roots = []
        site = api.portal.get()
        for rp in ("clients", "samples"):
            try:
                root = site.restrictedTraverse(rp)
                roots.append("/".join(root.getPhysicalPath()))
            except Exception:
                pass
        return roots or None

    @viewcache.memoize
    def _sampletype_title_by_uid(self, st_uid):
        pc = getToolByName(self.context, "portal_catalog")
        # 1) 首选按 UID 精确命中
        brains = pc.unrestrictedSearchResults(UID=st_uid)
        if brains:
            # Title 已是 unicode，直接返回
            return brains[0].Title

        # 2) 兼容路径限定（有的站点限制类型只在 setup/sampletypes 下）
        site_path = "/".join(self.context.getPhysicalRoot().getPhysicalPath())
        sampletypes_path = site_path + "/TCRx/setup/sampletypes"
        brains = pc.unrestrictedSearchResults(
            path={"query": sampletypes_path, "depth": 2},
            UID=st_uid,
        )
        if brains:
            return brains[0].Title

        return st_uid

    @viewcache.memoize
    def count_by_sampletype(self):
        """从样本 catalog（senaite_catalog_sample）里统计每个 SampleTypeUID 的样本数量。
        只统计真实存在的样本条目（AnalysisRequest），并限制在 /clients 与 /samples 路径下。
        返回：{ uid: count }，按 count 降序。
        """
        cat = self._sample_catalog()
        roots = self._sample_roots()

        params_base = {
            "portal_type": "AnalysisRequest",
            "is_active": True,
            # 提升性能：仅取需要的索引字段（不同站点可能不支持 columns，容错处理）
            # "columns": ["getSampleTypeUID"],
        }

        brains = []
        if roots:
            for r in roots:
                p = dict(params_base)
                p["path"] = {"query": r, "depth": 8}
                try:
                    brains.extend(cat.unrestrictedSearchResults(**p))
                except Exception:
                    # 兼容没有 unrestrictedSearchResults 的 catalog
                    brains.extend(cat(**p))
        else:
            try:
                brains = cat.unrestrictedSearchResults(**params_base)
            except Exception:
                brains = cat(**params_base)

        counts = {}
        for b in brains:
            # 先从 brain 取
            uid = getattr(b, "getSampleTypeUID", None)
            if callable(uid):
                try:
                    uid = uid()
                except Exception:
                    uid = None

            # 取不到再 fallback 到对象
            if not uid:
                try:
                    ob = b.getObject()
                except Exception:
                    ob = None
                if ob is not None:
                    getter = getattr(ob, "getSampleTypeUID", None)
                    if callable(getter):
                        try:
                            uid = getter()
                        except Exception:
                            uid = None

            if not uid:
                continue

            counts[uid] = counts.get(uid, 0) + 1

        # 按数量降序返回（保持 dict），便于前端按多到少显示
        return dict(sorted(counts.items(), key=lambda kv: kv[1], reverse=True))

    def get_sampletypes_section(self):

        panels = []

        # 1) 统计：来自样本 catalog
        counts = self.count_by_sampletype()
        if not counts:
            self._dbg("sampletypes: no counts from sample-catalog")
            return {'id': 'sampletypes', 'title': '样本类型', 'panels': panels}

        # 2) 构造 UID -> (Title, URL) 的映射（走 setup catalog + 对象 Title()）
        uid2info = self._sampletype_title_url_map()
        dash_url = api.portal.get().absolute_url() + "/senaite-dashboard"
        # 3) 组装卡片
        for uid, n in counts.items():
            title, link = uid2info.get(uid, (uid, api.portal.get().absolute_url() + '/samples'))
            panels.append({
                'type': 'simple-panel',
                'class': 'informative',
                'number': title,
                'legend': '',
                'description': u'%d 个样本' % n,  # 样本统计数量
                'link': '{}?st={}'.format(dash_url, uid),
            })

        return {
            'id': 'sampletypes',
            'title': '样本类型',
            'panels': panels,
        }

    def _samples_path(self, **params):
        """
        生成 samples 列表页的相对链接，Py2/3 兼容，并输出详细调试日志。
        """
        base = 'samples'
        raw_params = dict(params)  # 原始入参备份
        # 去掉空值
        params = {k: v for k, v in params.items() if v not in (None, '', [])}

        if not params:
            url = base
            LOG.info("[DASH.samples_path] base-only | url=%s", url)
            return url

        # Py2/3 兼容的 urlencode
        try:
            # Py2
            from urllib import urlencode
            PY2 = True
        except ImportError:
            # Py3
            from urllib.parse import urlencode
            PY2 = False

        enc_params = params
        if PY2:
            # Py2 下把 unicode 转为 utf-8，列表也要逐项处理
            def _enc(x):
                try:
                    is_unicode = isinstance(x, unicode)  # noqa: F821
                except NameError:
                    is_unicode = False
                if is_unicode:
                    return x.encode('utf-8')
                if isinstance(x, (list, tuple)):
                    return [_enc(i) for i in x]
                return x

            enc_params = {k: _enc(v) for k, v in params.items()}

        query = urlencode(enc_params, doseq=True)
        url = '%s?%s' % (base, query)

        # —— 关键 DEBUG ——
        LOG.info(
            "[DASH.samples_path] PY%s | raw=%s | cleaned=%s | encoded=%s | url=%s",
            "2" if PY2 else "3",
            raw_params,  # 原始入参
            params,  # 清洗后的参数（去掉空值）
            enc_params,  # 编码后（Py2 时会变化；Py3 同 params）
            url
        )
        return url


class DashboardViewPermissionUpdate(BrowserView):
    """
    Updates the values in 'senaite.core.dashboard_panels_visibility' registry.
    """

    def __call__(self):
        self._dbg("DASHBOARD VIEW LOADED", p=self.periodicity)

        protect.CheckAuthenticator(self.request)
        # Getting values from post
        section_name = self.request.get('section_name', None)
        if section_name is None:
            return None
        role_id = self.request.get('role_id', None)
        if role_id is None:
            return None
        check_state = self.request.get('check_state', None)
        if check_state is None:
            return None
        elif check_state == 'false':
            check_state = 'no'
        else:
            check_state = 'yes'
        # Update registry
        registry_info = get_dashboard_registry_record()
        pairs = get_dashboard_panels_visibility_by_section(section_name)
        role_permissions = list()
        for pair in pairs:
            visibility = pair[1]
            if pair[0] == role_id:
                visibility = check_state
            value = '{0},{1}'.format(pair[0], visibility)
            role_permissions.append(value)
        role_permissions = ','.join(role_permissions)
        # Set permissions string into dict
        registry_info[section_name] = get_unicode(role_permissions)
        set_dashboard_registry_record(registry_info)

        return True
