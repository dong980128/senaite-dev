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

import json
import this
from collections import OrderedDict
from copy import copy
from copy import deepcopy
from datetime import datetime
from datetime import timedelta
from operator import itemgetter

import re

from bika.lims import api
from bika.lims import bikaMessageFactory as _
from bika.lims import deprecated
from bika.lims import logger
from bika.lims.api.analysis import get_formatted_interval
from bika.lims.api.analysis import is_out_of_range
from bika.lims.api.analysis import is_result_range_compliant
from bika.lims.browser.analyses.utils import (get_selected_targets_from_conditions, filter_interim_fields_by_targets)
from bika.lims.config import LDL
from bika.lims.config import UDL
from bika.lims.interfaces import IAnalysisRequest
from bika.lims.interfaces import IFieldIcons
from bika.lims.interfaces import IReferenceAnalysis
from bika.lims.interfaces import IRoutineAnalysis
from bika.lims.interfaces import ISubmitted
from bika.lims.utils import check_permission
from bika.lims.utils import format_supsub
from bika.lims.utils import formatDecimalMark
from bika.lims.utils import get_fas_ico
from bika.lims.utils import get_image
from bika.lims.utils import get_link
from bika.lims.utils import get_link_for
from bika.lims.utils.analysis import format_uncertainty
from DateTime import DateTime
from plone.memoize import view as viewcache
from Products.Archetypes.config import REFERENCE_CATALOG
from Products.CMFPlone.utils import safe_unicode
from senaite.app.listing import ListingView
from senaite.core.api import dtime
from senaite.core.catalog import ANALYSIS_CATALOG
from senaite.core.catalog import SETUP_CATALOG
from senaite.core.i18n import translate as t
from senaite.core.permissions import EditFieldResults
from senaite.core.permissions import EditResults
from senaite.core.permissions import FieldEditAnalysisConditions
from senaite.core.permissions import FieldEditAnalysisHidden
from senaite.core.permissions import FieldEditAnalysisResult
from senaite.core.permissions import TransitionVerify
from senaite.core.permissions import ViewResults
from senaite.core.permissions import ViewRetractedAnalyses
from senaite.core.registry import get_registry_record
from zope.component import getAdapters
from zope.component import getMultiAdapter
from Products.CMFCore.utils import getToolByName
from bika.lims.api.user import get_current_contact, get_allowed_keywords

# --- Unicode helpers (Py2-safe) ---
_BOM = u"\ufeff"


def _u(x):
    """Return unicode safely (Py2):
    - bytes -> try utf-8, fallback latin-1
    - strip BOM if present
    - any other -> unicode(str(x))
    """
    try:
        # 已是 unicode
        if isinstance(x, unicode):
            s = x
        elif isinstance(x, str):  # bytes
            try:
                s = x.decode("utf-8")
            except Exception:
                s = x.decode("latin-1", "ignore")
        else:
            # 其它类型
            try:
                s = unicode(x)
            except Exception:
                s = unicode(str(x), "utf-8", "ignore")
        # 去掉 BOM
        if s and s[:1] == _BOM:
            s = s.lstrip(_BOM)
        return s
    except Exception:
        return u""


def _json_loads_safe(s):
    """json.loads，自动处理 bytes/unicode + BOM；解析失败返回 None"""
    try:
        us = _u(s)
        if not us:
            return None
        # json 规范要求 UTF-8；此处 us 已是 unicode，无需再解码
        return json.loads(us)
    except Exception:
        return None


class AnalysesView(ListingView):
    """Displays a list of Analyses in a table.

    Visible InterimFields from all analyses are added to self.columns[].
    Keyword arguments are passed directly to senaite_catalog_analysis.
    """

    def __init__(self, context, request, **kwargs):
        super(AnalysesView, self).__init__(context, request, **kwargs)

        # prepare the content filter of this listing
        self.contentFilter = dict(kwargs)
        self.contentFilter.update({
            "portal_type": "Analysis",
            "sort_on": "sortable_title",
            "sort_order": "ascending",
        })

        # set the listing view config
        self.catalog = ANALYSIS_CATALOG
        self.sort_order = "ascending"
        self.context_actions = {}

        self.show_select_row = False
        self.show_select_column = False
        self.show_column_toggles = False
        self.pagesize = 9999999
        self.form_id = "analyses_form"
        self.context_active = api.is_active(context)
        self.interim_fields = {}
        self.interim_columns = OrderedDict()
        self.specs = {}
        self.bsc = api.get_tool(SETUP_CATALOG)
        self.portal = api.get_portal()
        self.portal_url = api.get_url(self.portal)
        self.rc = api.get_tool(REFERENCE_CATALOG)
        self.dmk = context.bika_setup.getResultsDecimalMark()
        self.scinot = context.bika_setup.getScientificNotationResults()
        self.categories = []
        self.expand_all_categories = True
        self.now = datetime.now()

        # each editable item needs it's own allow_edit
        # which is a list of field names.
        self.allow_edit = False
        self.columns = OrderedDict((
            # Although 'created' column is not displayed in the list (see
            # review_states to check the columns that will be rendered), this
            # column is needed to sort the list by create date
            ("created", {
                "title": _("Date Created"),
                "toggle": False}),
            ("Service", {
                "title": _("Analysis"),
                "attr": "Title",
                "index": "sortable_title",
                "sortable": False}),
            ("Analyst", {
                "title": _("Analyst"),
                "sortable": False,
                "ajax": True,
                "on_change": "_on_analyst_change",
                "toggle": True,
            }),
            ("DetectionLimitOperand", {
                "title": _("DL"),
                "sortable": False,
                "ajax": True,
                "autosave": True,
                "toggle": False}),
            # ("Result", {
            #     "title": _("Result"),
            #     "input_width": "6",
            #     "input_class": "ajax_calculate numeric",
            #     "ajax": True,
            #     "sortable": False}),
            ("Uncertainty", {
                "title": _("+-"),
                "ajax": True,
                "sortable": False}),
            ("Unit", {
                "title": _("Unit"),
                "sortable": False,
                "ajax": True,
                "on_change": "_on_unit_change",
                "toggle": True}),
            ("Specification", {
                "title": _("Specification"),
                "sortable": False}),
            ("retested", {
                "title": _("Retested"),
                "type": "boolean",
                "sortable": False}),
            ("Method", {
                "title": _("Method"),
                "sortable": False,
                "ajax": True,
                "type": "multiselect",
                "on_change": "_on_method_change",
                "toggle": True}),
            ("Instrument", {
                "title": _("Instrument"),
                "ajax": True,
                "sortable": False,
                "toggle": True}),
            ("Calculation", {
                "title": _("Calculation"),
                "sortable": False,
                "toggle": False}),
            ("Attachments", {
                "title": _("Attachments"),
                "sortable": False}),
            ("SubmittedBy", {
                "title": _("Submitter"),
                "sortable": False}),
            ("ResultCaptureDate", {
                "title": _("Captured"),
                "index": "getResultCaptureDate",
                "type": "datetime",
                "max": self.now.strftime("%Y-%m-%d"),
                "ajax": True,
                "sortable": False}),
            ("DueDate", {
                "title": _("Due Date"),
                "index": "getDueDate",
                "sortable": False}),
            ("state_title", {
                "title": _("Status"),
                "sortable": False}),
            ("Hidden", {
                "title": _("Hidden"),
                "toggle": True,
                "sortable": False,
                "ajax": True,
                "type": "boolean"}),
        ))

        # Inject Remarks column for listing
        if self.analysis_remarks_enabled():
            self.columns["Remarks"] = {
                "title": "Remarks",
                "toggle": False,
                "sortable": False,
                "type": "remarks",
                "ajax": True,
            }

        self.review_states = [
            {
                "id": "default",
                "title": _("Valid"),
                "contentFilter": {
                    "review_state": [
                        "registered",
                        "unassigned",
                        "assigned",
                        "to_be_verified",
                        "verified",
                        "published",
                    ]
                },
                "columns": self.columns.keys()
            },
            {
                "id": "invalid",
                "contentFilter": {
                    "review_state": [
                        "cancelled",
                        "retracted",
                        "rejected",
                    ]
                },
                "title": _("Invalid"),
                "columns": self.columns.keys(),
            },
            {
                "id": "all",
                "title": _("All"),
                "contentFilter": {},
                "columns": self.columns.keys()
            },
        ]

    def update(self):
        """Update hook
        """
        super(AnalysesView, self).update()
        self.load_analysis_categories()
        self.append_partition_filters()
        if self.analysis_categories_enabled():
            self.show_categories = True

        key = next((k for k in ("Analyst", "SubmittedBy") if k in self.columns), None)
        if not key:
            return
        # 2) 仅把“检验者”顶到最前，其它列保持原相对顺序
        cols = self.columns
        self.columns = OrderedDict([(key, cols[key])] + [(k, v) for k, v in cols.items() if k != key])

        # 3) 同步各 review_state 的列顺序（核心点）
        for rs in getattr(self, "review_states", []):
            lst = rs.get("columns") or list(cols.keys())
            rs["columns"] = ["Service", key] + [c for c in lst if c not in ("Service", key)]

    def before_render(self):
        """Before render hook
        """
        super(AnalysesView, self).before_render()
        self.request.set("disable_plone.rightcolumn", 1)

    def _is_manager_user(self):
        """是否为管理员/实验室主管（始终放行）"""
        user = api.get_current_user()
        roles = set(user.getRoles())
        return bool({"Manager", "LabManager"} & roles)

    def _is_manage_user(self):
        user = api.get_current_user()
        roles = set(user.getRoles())
        return bool({"Manager"} & roles)

    def _is_assigned_to_me(self, obj):
        """是否把该分析指派给了我"""
        user = api.get_current_user()
        current_id = getattr(user, "getId", lambda: "")()
        assigned_to = getattr(obj, "getAnalyst", lambda: None)()
        assigned_id = getattr(assigned_to, "getId", lambda: None)() or assigned_to
        return bool(assigned_id) and (assigned_id == current_id)

    @viewcache.memoize
    def get_default_columns_order(self):
        """Return the default column order from the registry

        :returns: List of column keys
        """
        name = "sampleview_analysis_columns_order"
        columns_order = get_registry_record(name, default=[]) or []
        return columns_order

    def reorder_analysis_columns(self):
        """Reorder analysis columns based on registry configuration
        """
        columns_order = self.get_default_columns_order()
        if not columns_order:
            return
        # compute columns that are missing in the config
        missing_columns = filter(
            lambda col: col not in columns_order, self.columns.keys())
        # prepare the new sort order for the columns
        ordered_columns = columns_order + missing_columns

        # set the order in each review state
        for rs in self.review_states:
            # set a copy of the new ordered columns list
            rs["columns"] = ordered_columns[:]

    def calculate_interim_columns_position(self, review_state):
        """Calculate at which position the interim columns should be inserted
        """
        columns = review_state.get("columns", [])
        if "AdditionalValues" in columns:
            return columns.index("AdditionalValues")
        if "Result" in columns:
            return columns.index("Result")
        return len(columns)

    @property
    @viewcache.memoize
    def senaite_theme(self):
        return getMultiAdapter(
            (self.context, self.request),
            name="senaite_theme")

    @property
    @viewcache.memoize
    def show_partitions(self):
        """Returns whether the partitions must be displayed or not
        """
        if api.get_current_client():
            # Current user is a client contact
            return api.get_setup().getShowPartitions()
        return True

    @viewcache.memoize
    def analysis_remarks_enabled(self):
        """Check if analysis remarks are enabled
        """
        return self.context.bika_setup.getEnableAnalysisRemarks()

    @viewcache.memoize
    def analysis_categories_enabled(self):
        """Check if analyses should be grouped by category
        """
        # setting applies only for samples
        if not IAnalysisRequest.providedBy(self.context):
            return False
        setup = api.get_senaite_setup()
        return setup.getCategorizeSampleAnalyses()

    @viewcache.memoize
    def has_permission(self, permission, obj=None):
        """Returns if the current user has rights for the permission passed in

        :param permission: permission identifier
        :param obj: object to check the permission against
        :return: True if the user has rights for the permission passed in
        """
        if not permission:
            logger.warn("None permission is not allowed")
            return False
        if obj is None:
            return check_permission(permission, self.context)
        return check_permission(permission, self.get_object(obj))

    @viewcache.memoize
    def is_analysis_edition_allowed(self, analysis_brain):
        """Returns if the analysis passed in can be edited by the current user

        :param analysis_brain: Brain that represents an analysis
        :return: True if the user can edit the analysis, otherwise False
        """
        if not self.context_active:
            # The current context must be active. We cannot edit analyses from
            # inside a deactivated Analysis Request, for instance
            return False

        analysis_obj = self.get_object(analysis_brain)
        if analysis_obj.getPointOfCapture() == 'field':
            # This analysis must be captured on field, during sampling.
            if not self.has_permission(EditFieldResults, analysis_obj):
                # Current user cannot edit field analyses.
                return False

        elif not self.has_permission(EditResults, analysis_obj):
            # The Point of Capture is 'lab' and the current user cannot edit
            # lab analyses.
            return False

        # Check if the user is allowed to enter a value to to Result field
        if not self.has_permission(FieldEditAnalysisResult, analysis_obj):
            return False

        # === 新增：指派权限===
        obj = self.get_object(analysis_brain)

        # Manage和LabManager角色放行；Analyst必须是“被指派的”
        if not self._is_manager_user() and not self._is_assigned_to_me(obj):
            return False

        return True

    @viewcache.memoize
    def is_result_edition_allowed(self, analysis_brain):
        """Checks if the edition of the result field is allowed

        :param analysis_brain: Brain that represents an analysis
        :return: True if the user can edit the result field, otherwise False
        """

        # Always check general edition first
        if not self.is_analysis_edition_allowed(analysis_brain):
            return False

        # Get the ananylsis object
        obj = self.get_object(analysis_brain)

        if not obj.getDetectionLimitOperand():
            # This is a regular result (not a detection limit)
            return True

        # Detection limit selector is enabled in the Analysis Service
        if obj.getDetectionLimitSelector():
            # Manual detection limit entry is *not* allowed
            if not obj.getAllowManualDetectionLimit():
                return False

        return True

    @viewcache.memoize
    def is_uncertainty_edition_allowed(self, analysis_brain):
        """Checks if the edition of the uncertainty field is allowed

        :param analysis_brain: Brain that represents an analysis
        :return: True if the user can edit the result field, otherwise False
        """

        # Only allow to edit the uncertainty if result edition is allowed
        if not self.is_result_edition_allowed(analysis_brain):
            return False

        # Get the ananylsis object
        obj = self.get_object(analysis_brain)

        # Manual setting of uncertainty is not allowed
        if not obj.getAllowManualUncertainty():
            return False

        # Result is a detection limit -> uncertainty setting makes no sense!
        if obj.getDetectionLimitOperand() in [LDL, UDL]:
            return False

        return True

    @viewcache.memoize
    def is_analysis_conditions_edition_allowed(self, analysis_brain):
        """Returns whether the conditions of the analysis can be edited or not
        """
        # Check if permission is granted for the given analysis
        obj = self.get_object(analysis_brain)

        if IReferenceAnalysis.providedBy(obj):
            return False

        if not self.has_permission(FieldEditAnalysisConditions, obj):
            return False

        # Omit analysis does not have conditions set
        if not obj.getConditions(empties=True):
            return False

        return True

    @viewcache.memoize
    def is_manual_result_capture_date_allowed(self):
        """Returns whether it is allowed to set the result capture date manually
        """
        setup = api.get_senaite_setup()
        return setup.getAllowManualResultCaptureDate()

    def get_instrument(self, analysis_brain):
        """Returns the instrument assigned to the analysis passed in, if any

        :param analysis_brain: Brain that represents an analysis
        :return: Instrument object or None
        """
        obj = self.get_object(analysis_brain)
        return obj.getInstrument()

    def get_calculation(self, analysis_brain):
        """Returns the calculation assigned to the analysis passed in, if any

        :param analysis_brain: Brain that represents an analysis
        :return: Calculation object or None
        """
        obj = self.get_object(analysis_brain)
        return obj.getCalculation()

    @viewcache.memoize
    def get_object(self, brain_or_object_or_uid):
        """Get the full content object. Returns None if the param passed in is
        not a valid, not a valid object or not found

        :param brain_or_object_or_uid: UID/Catalog brain/content object
        :returns: content object
        """
        return api.get_object(brain_or_object_or_uid, default=None)

    def get_methods_vocabulary(self, analysis_brain):
        """Returns a vocabulary with all the methods available for the passed in
        analysis, either those assigned to an instrument that are capable to
        perform the test (option "Allow Entry of Results") and those assigned
        manually in the associated Analysis Service.

        The vocabulary is a list of dictionaries. Each dictionary has the
        following structure:

            {'ResultValue': <method_UID>,
             'ResultText': <method_Title>}

        :param analysis_brain: A single Analysis brain
        :type analysis_brain: CatalogBrain
        :returns: A list of dicts
        """
        vocab = []
        obj = self.get_object(analysis_brain)
        # default_method = obj.getMethod()
        default_method = obj.getRawMethod()

        methods = obj.getAllowedMethods()
        empty_option = {"ResultValue": "", "ResultText": _("None")}
        for method in methods:
            vocab.append({
                "ResultValue": api.get_uid(method),
                "ResultText": api.get_title(method),
            })
        # allow empty option if we have no allowed methods
        if not methods:
            vocab = [empty_option]
        # allow empty option if the default method is set to "None"
        # elif default_method is None:
        elif not default_method:
            vocab.insert(0, empty_option)
        return vocab

    def get_unit_vocabulary(self, analysis_brain):
        """Returns a vocabulary with all the units available for the passed in
        analysis.

        The vocabulary is a list of dictionaries. Each dictionary has the
        following structure:

            {'ResultValue': <unit>,
             'ResultText': <unit>}

        :param analysis_brain: A single Analysis brain
        :type analysis_brain: CatalogBrain
        :returns: A list of dicts
        """
        obj = self.get_object(analysis_brain)
        # Get unit choices
        unit_choices = obj.getUnitChoices()
        vocab = []
        for unit in unit_choices:
            vocab.append({
                "ResultValue": unit['value'],
                "ResultText": unit['value'],
            })
        return vocab

    def get_instruments_vocabulary(self, analysis, method=None):
        """Returns a vocabulary with the valid and active instruments available
        for the analysis passed in.

        If the option "Allow instrument entry of results" for the Analysis
        is disabled, the function returns an empty vocabulary.

        If the analysis passed in is a Reference Analysis (Blank or Control),
        the vocabulary, the invalid instruments will be included in the
        vocabulary too.

        The vocabulary is a list of dictionaries. Each dictionary has the
        following structure:

            {'ResultValue': <instrument_UID>,
             'ResultText': <instrument_Title>}

        :param analysis: A single Analysis or ReferenceAnalysis
        :type analysis_brain: Analysis or.ReferenceAnalysis
        :return: A vocabulary with the instruments for the analysis
        :rtype: A list of dicts: [{'ResultValue':UID, 'ResultText':Title}]
        """
        obj = self.get_object(analysis)
        # get the allowed interfaces from the analysis service
        instruments = obj.getAllowedInstruments()
        # if no method is passed, get the assigned method of the analyis
        if method is None:
            method = obj.getMethod()

        # # check if the analysis has a method
        # if method:
        #     # supported instrument from the method
        #     method_instruments = method.getInstruments()
        #     # allow only method instruments that are set in service
        #     instruments = list(
        #         set(instruments).intersection(method_instruments))

        # check if the analysis has method(s)
        methods = api.to_list(method)

        method_instruments = []
        for m in methods:
            if not m:
                continue
            method_instruments.extend(api.to_list(m.getInstruments()))

        # 去重后做交集
        method_instruments = set(method_instruments)
        instruments = list(set(instruments).intersection(method_instruments))

        # If the analysis is a QC analysis, display all instruments, including
        # those uncalibrated or for which the last QC test failed.
        is_qc = api.get_portal_type(obj) == "ReferenceAnalysis"

        vocab = []
        for instrument in instruments:
            uid = api.get_uid(instrument)
            title = api.safe_unicode(api.get_title(instrument))
            # append all valid instruments
            if instrument.isValid():
                vocab.append({
                    "ResultValue": uid,
                    "ResultText": title,
                })
            elif is_qc:
                # Is a QC analysis, include instrument also if is not valid
                if instrument.isOutOfDate():
                    title = _(u"{} (Out of date)".format(title))
                vocab.append({
                    "ResultValue": uid,
                    "ResultText": title,
                })
            elif instrument.isOutOfDate():
                # disable out of date instruments
                title = _(u"{} (Out of date)".format(title))
                vocab.append({
                    "disabled": True,
                    "ResultValue": None,
                    "ResultText": title,
                })

        # sort the vocabulary
        vocab = list(sorted(vocab, key=itemgetter("ResultText")))
        # prepend empty item
        vocab = [{"ResultValue": "", "ResultText": _("None")}] + vocab

        return vocab

    def load_analysis_categories(self):
        # Getting analysis categories
        bsc = api.get_tool('senaite_catalog_setup')
        analysis_categories = bsc(portal_type="AnalysisCategory",
                                  sort_on="sortable_title")
        # Sorting analysis categories
        self.analysis_categories_order = dict([
            (b.Title, "{:04}".format(a)) for a, b in
            enumerate(analysis_categories)])

    def append_partition_filters(self):
        """Append additional review state filters for partitions
        """

        # view is used for instrument QC view as well
        if not IAnalysisRequest.providedBy(self.context):
            return

        # check if the sample has partitions
        partitions = self.context.getDescendants()

        # root partition w/o partitions
        if not partitions:
            return

        new_states = []
        valid_states = [
            "registered",
            "unassigned",
            "assigned",
            "to_be_verified",
            "verified",
            "published",
        ]

        if self.context.isRootAncestor():
            root_id = api.get_id(self.context)
            new_states.append({
                "id": root_id,
                "title": root_id,
                "contentFilter": {
                    "getAncestorsUIDs": api.get_uid(self.context),
                    "review_state": valid_states,
                    "path": {
                        "query": api.get_path(self.context),
                        "level": 0,
                    },
                },
                "columns": self.columns.keys(),
            })

        for partition in partitions:
            part_id = api.get_id(partition)
            new_states.append({
                "id": part_id,
                "title": part_id,
                "contentFilter": {
                    "getAncestorsUIDs": api.get_uid(partition),
                    "review_state": valid_states,
                },
                "columns": self.columns.keys(),
            })

        for state in sorted(new_states, key=lambda s: s.get("id")):
            if state not in self.review_states:
                self.review_states.append(state)

    def isItemAllowed(self, obj):
        """Checks if the passed in Analysis must be displayed in the list.
        :param obj: A single Analysis brain or content object
        :type obj: ATContentType/CatalogBrain
        :returns: True if the item can be added to the list.
        :rtype: bool
        """
        if not obj:
            return False

        # Does the user has enough privileges to see retracted analyses?
        if obj.review_state == 'retracted' and \
                not self.has_permission(ViewRetractedAnalyses):
            return False

        return True

    def folderitem(self, obj, item, index):
        """Prepare a data item for the listing.

        :param obj: The catalog brain or content object
        :param item: Listing item (dictionary)
        :param index: Index of the listing item
        :returns: Augmented listing data item
        """

        item['Service'] = obj.Title
        item['class']['service'] = 'service_title'
        item['service_uid'] = obj.getServiceUID
        item['Keyword'] = obj.getKeyword
        item['Unit'] = format_supsub(obj.getUnit) if obj.getUnit else ''
        item['retested'] = obj.getRetestOfUID and True or False
        item['replace']['Service'] = '<strong>{}</strong>'.format(obj.Title)

        # Append info link before the service
        # see: bika.lims.site.coffee for the attached event handler
        item["before"]["Service"] = get_link(
            "analysisservice_info?service_uid={}&analysis_uid={}"
            .format(obj.getServiceUID, obj.UID),
            value="<i class='fas fa-info-circle'></i>",
            css_class="overlay_panel", tabindex="-1")

        # Append conditions link before the analysis
        # see: bika.lims.site.coffee for the attached event handler
        if self.is_analysis_conditions_edition_allowed(obj):
            url = api.get_url(self.context)
            url = "{}/set_analysis_conditions?uid={}".format(url, obj.UID)
            ico = "<i class='fas fa-list' style='padding-top: 5px;'/>"
            conditions = get_link(url, value=ico, css_class="overlay_panel",
                                  tabindex="-1")
            info = item["before"]["Service"]
            item["before"]["Service"] = "<br/>".join([info, conditions])

        # Note that getSampleTypeUID returns the type of the Sample, no matter
        # if the sample associated to the analysis is a regular Sample (routine
        # analysis) or if is a Reference Sample (Reference Analysis). If the
        # analysis is a duplicate, it returns the Sample Type of the sample
        # associated to the source analysis.
        item['st_uid'] = obj.getSampleTypeUID

        # Fill item's category
        self._folder_item_category(obj, item)
        # Fill item's row class
        self._folder_item_css_class(obj, item)
        # Fill result and/or result options
        self._folder_item_result(obj, item)
        # Fill calculation and interim fields
        self._folder_item_calculation(obj, item)
        # Fill unit field
        self._folder_item_unit(obj, item)
        # Fill method
        self._folder_item_method(obj, item)
        # Fill instrument
        self._folder_item_instrument(obj, item)
        # Fill analyst
        self._folder_item_analyst(obj, item)
        # Fill submitted by
        self._folder_item_submitted_by(obj, item)
        # Fill attachments
        self._folder_item_attachments(obj, item)
        # Fill uncertainty
        self._folder_item_uncertainty(obj, item)
        # Fill Detection Limits
        self._folder_item_detection_limits(obj, item)
        # Fill Specifications
        self._folder_item_specifications(obj, item)
        self._folder_item_out_of_range(obj, item)
        self._folder_item_result_range_compliance(obj, item)
        # Fill Partition
        self._folder_item_partition(obj, item)
        # Fill Due Date and icon if late/overdue
        self._folder_item_duedate(obj, item)
        # Fill verification criteria
        self._folder_item_verify_icons(obj, item)
        # Fill worksheet anchor/icon
        self._folder_item_assigned_worksheet(obj, item)
        # Fill accredited icon
        self._folder_item_accredited_icon(obj, item)
        # Fill hidden field (report visibility)
        self._folder_item_report_visibility(obj, item)
        # Renders additional icons to be displayed
        self._folder_item_fieldicons(obj)
        # Renders remarks toggle button
        self._folder_item_remarks(obj, item)
        # Renders the analysis conditions
        self._folder_item_conditions(obj, item)
        # Fill maximum holding time warnings
        self._folder_item_holding_time(obj, item)

        return item

    # def folderitems(self):
    #     self.before_render()
    #
    #     # 取出原始的分析 items（还没过滤）
    #     items = super(AnalysesView, self).folderitems()
    #
    #     #  添加空字段支持，避免前端渲染时字段缺失
    #     for item in items:
    #         for field in self.interim_columns:
    #             if field not in item:
    #                 item[field] = ""
    #         item["help"] = item.get("help", {})
    #
    #     #    记录当前中间字段列，后续需要按“最初的中间列集合”做清理
    #     #    注意：dict.keys() 在 Py3 是 view，需要 list(...) 才能 reverse
    #     interim_keys = list(self.interim_columns.keys())
    #     interim_keys.reverse()
    #     original_interim_keys = set(interim_keys)  # ★ 仅把“最初的中间列”列为可清理候选
    #
    #     # 将中间字段列注入 self.columns（）
    #     for col_id in interim_keys:
    #         if col_id not in self.columns:
    #             self.columns[col_id] = {
    #                 "title": self.interim_columns[col_id],
    #                 "input_width": "6",
    #                 "input_class": "ajax_calculate numeric",
    #                 "sortable": False,
    #                 "toggle": True,
    #                 "ajax": True,
    #             }
    #
    #     # 当前用户、角色、用户名、全名
    #     current_user = api.get_current_user()
    #     current_roles = set(current_user.getRoles())
    #     current_username = current_user.getUserName()
    #     current_fullname = getattr(current_user, "getProperty", lambda *a, **k: "")(
    #         "fullname", ""
    #     ) or current_username
    #
    #     if {"Manager", "LabManager", "Analyst"} & current_roles:
    #         self.allow_edit = True
    #
    #     # 若允许编辑，把中间字段列插入各 review_state 的 columns 中
    #     if self.allow_edit:
    #         new_states = []
    #         for state in self.review_states:
    #             pos = self.calculate_interim_columns_position(state)
    #             for col_id in interim_keys:
    #                 if col_id not in state["columns"]:
    #                     state["columns"].insert(pos, col_id)
    #             new_states.append(state)
    #         self.review_states = new_states
    #         self.show_select_column = True
    #
    #     # 分类排序
    #     if self.show_categories:
    #         self.categories = map(lambda x: x[0],
    #                               sorted(self.categories, key=lambda x: x[1]))
    #     else:
    #         self.categories.sort()
    #
    #     # 列显示开关
    #     if "Method" in self.columns:
    #         self.columns["Method"]["toggle"] = self.is_method_column_required(items)
    #     if "Instrument" in self.columns:
    #         self.columns["Instrument"]["toggle"] = self.is_instrument_column_required(items)
    #     if "Unit" in self.columns:
    #         self.columns["Unit"]["toggle"] = self.is_unit_selection_column_required(items)
    #
    #     def is_allowed(item):
    #         obj = self.get_object(item.get("obj"))
    #         if obj is None:
    #             return False
    #
    #         # 管理角色始终可见
    #         if "Manager" in current_roles or "Publisher" in current_roles:
    #             return True
    #
    #         wf = getToolByName(self.context, "portal_workflow")
    #         state = wf.getInfoFor(obj, "review_state", "") or ""
    #
    #         INVALID_STATES = {"unassigned", ""}
    #
    #         assigned_to = getattr(obj, "getAnalyst", lambda: None)()
    #         assigned_id = getattr(assigned_to, "getId", lambda: None)() or assigned_to
    #
    #         # 分给我而且状态正常 → 直接看
    #         if assigned_id == current_username:
    #             # if assigned_id == current_username and state not in INVALID_STATES:
    #             return True
    #
    #         # 其余走 Allowed 关键字过滤
    #         try:
    #             keyword = obj.getKeyword()
    #         except Exception:
    #             return False
    #
    #         allowed_keywords = get_allowed_keywords(self.context)
    #         if allowed_keywords == "__ALL__":
    #             return True
    #         return keyword in allowed_keywords
    #
    #     # 应用过滤
    #     items = [item for item in items if is_allowed(item)]
    #
    #     wf = getToolByName(self.context, "portal_workflow")
    #     for item in items:
    #         obj = self.get_object(item.get("obj"))
    #         assigned_to = getattr(obj, "getAnalyst", lambda: None)()
    #         assigned_id = getattr(assigned_to, "getId", lambda: None)() or assigned_to
    #         state = wf.getInfoFor(obj, "review_state", "") or ""
    #
    #         # 默认禁用
    #         item["disabled"] = True
    #
    #         if {"Manager", "LabManager"} & current_roles:
    #             item["disabled"] = False
    #         elif ("Analyst" in current_roles
    #               and assigned_id == current_username
    #               and state not in ("submitted", "verified", "published")):
    #             item["disabled"] = False
    #         elif ("Analyst" in current_roles
    #               and not assigned_id
    #               and state in ("unassigned", "registered")):
    #             item["disabled"] = False
    #
    #     valid_uids = [item.get("uid") for item in items if item.get("uid")]
    #     self.interim_fields = {
    #         uid: self.interim_fields[uid]
    #         for uid in valid_uids if uid in self.interim_fields
    #     }
    #
    #     used_interim_keys = set()
    #     self.interim_columns.clear()
    #     self.interim_columns_types ={} # 增加type
    #
    #     for uid in valid_uids:
    #         interim_list = self.interim_fields.get(uid, [])
    #         for interim in interim_list:
    #             key = interim.get("keyword")
    #             title = interim.get("title")
    #             rtype = interim.get("result_type")
    #             if key and title:
    #                 self.interim_columns[key] = title
    #                 used_interim_keys.add(key)
    #                 if rtype:
    #                     self.interim_columns_types =rtype
    #
    #     # 清理 columns 中“最初的中间字段列”里本次未使用到的键
    #     for key in list(self.columns.keys()):
    #         if key in original_interim_keys and key not in used_interim_keys:
    #             del self.columns[key]
    #
    #     # 现在再序列化，保证前端只拿到“过滤后的”中间字段数据
    #     self.json_interim_fields = json.dumps(self.interim_fields)
    #     return items
    def folderitems(self):
        self.before_render()
        items = super(AnalysesView, self).folderitems()
        original_interim_keys = list(self.interim_columns.keys())
        original_interim_keys.reverse()
        original_interim_keys = set(original_interim_keys)

        # 当前用户/角色信息，用来做行可见、可编辑判断
        current_user = api.get_current_user()
        current_roles = set(current_user.getRoles())
        current_username = current_user.getUserName()
        current_fullname = getattr(
            current_user, "getProperty", lambda *a, **k: ""
        )("fullname", "") or current_username

        # 默认不允许编辑，下面看角色再开
        self.allow_edit = False
        if {"Manager", "LabManager", "Analyst"} & current_roles:
            self.allow_edit = True

        # 定义行过滤函数
        def is_allowed(item):
            obj = self.get_object(item.get("obj"))
            if obj is None:
                return False

            # 管理角色始终可见
            # if "Manager" in current_roles or "Publisher" in current_roles:
            if "Manager" in current_roles:
                return True

            wf = getToolByName(self.context, "portal_workflow")
            state = wf.getInfoFor(obj, "review_state", "") or ""

            assigned_to = getattr(obj, "getAnalyst", lambda: None)()
            assigned_id = getattr(assigned_to, "getId", lambda: None)() or assigned_to

            # 分给我 → 可见
            if assigned_id == current_username:
                return True

            # 其余走关键字过滤
            try:
                keyword = obj.getKeyword()
            except Exception:
                return False

            allowed_keywords = get_allowed_keywords(self.context)
            if allowed_keywords == "__ALL__":
                return True
            return keyword in allowed_keywords

        # 应用过滤，只保留本用户能看的分析
        items = [item for item in items if is_allowed(item)]

        # 保留用户能看的分析数量，不会因为总分析数和看到的分析数差异
        try:
            visible = len(items)
            self.total = visible
            self.count = visible
        except Exception:
            pass

        # 设置每一行的 disabled
        wf = getToolByName(self.context, "portal_workflow")
        for item in items:
            obj = self.get_object(item.get("obj"))
            assigned_to = getattr(obj, "getAnalyst", lambda: None)()
            assigned_id = getattr(assigned_to, "getId", lambda: None)() or assigned_to
            state = wf.getInfoFor(obj, "review_state", "") or ""

            # 默认禁用
            item["disabled"] = True

            if {"Manager", "LabManager"} & current_roles:
                item["disabled"] = False
            elif ("Analyst" in current_roles
                  and assigned_id == current_username
                  and state not in ("submitted", "verified", "published")):
                item["disabled"] = False
            elif ("Analyst" in current_roles
                  and not assigned_id
                  and state in ("unassigned", "registered")):
                item["disabled"] = False

        # 这次列表里真正出现的分析 UID
        valid_uids = [item.get("uid") for item in items if item.get("uid")]

        # 把 self.interim_fields 里没在本次列表里出现的都剔除掉
        self.interim_fields = {
            uid: self.interim_fields[uid]
            for uid in valid_uids
            if uid in self.interim_fields
        }

        self.interim_columns.clear()
        self.interim_column_types = {}  # keyword -> result_type
        used_interim_keys = set()

        for uid in valid_uids:
            interim_list = self.interim_fields.get(uid, [])
            for interim in interim_list:
                key = interim.get("keyword")
                title = interim.get("title")
                rtype = interim.get("result_type")
                if key and title:
                    self.interim_columns[key] = title
                    used_interim_keys.add(key)
                    if rtype:
                        self.interim_column_types[key] = rtype

        # 把这次没用到的老中间列从 self.columns 里删掉
        for key in list(self.columns.keys()):
            if key in original_interim_keys and key not in used_interim_keys:
                del self.columns[key]

        # 把“这次真正用到的中间字段”注入到 self.columns 里，并且把 type 一起塞进去
        for col_id in reversed(list(self.interim_columns.keys())):
            if col_id not in self.columns:
                self.columns[col_id] = {
                    "title": self.interim_columns[col_id],
                    "input_width": "6",
                    "input_class": "ajax_calculate numeric",
                    "sortable": False,
                    "toggle": True,
                    "ajax": True,
                }
            # 不管是不是新建的列，都补一手 type
            rtype = self.interim_column_types.get(col_id)
            if rtype:
                self.columns[col_id]["type"] = rtype

        # 若允许编辑，还要把这些列插到各个 review_state
        if self.allow_edit:
            new_states = []
            for state in self.review_states:
                pos = self.calculate_interim_columns_position(state)
                for col_id in reversed(list(self.interim_columns.keys())):
                    if col_id not in state["columns"]:
                        state["columns"].insert(pos, col_id)
                new_states.append(state)
            self.review_states = new_states
            self.show_select_column = True

        # 分类排序
        if self.show_categories:
            self.categories = map(lambda x: x[0],
                                  sorted(self.categories, key=lambda x: x[1]))
        else:
            self.categories.sort()

        # 列显示开关
        if "Method" in self.columns:
            self.columns["Method"]["toggle"] = self.is_method_column_required(items)
        if "Instrument" in self.columns:
            self.columns["Instrument"]["toggle"] = self.is_instrument_column_required(items)
        if "Unit" in self.columns:
            self.columns["Unit"]["toggle"] = self.is_unit_selection_column_required(items)

        # # 日志确认后端已经把 type 带回来了(测试用)
        # for col_id, title in self.interim_columns.items():
        #     print("[AnalysesView] interim col:", col_id,
        #           "title:", title,
        #           "type:", self.interim_column_types.get(col_id))

        # 17. 最后把“过滤后的中间字段”序列化给前端
        self.json_interim_fields = json.dumps(self.interim_fields)
        return items

    def render_unit(self, unit, css_class=None):
        """Render HTML element for unit
        """
        if css_class is None:
            css_class = "unit d-inline-block py-2 small text-secondary text-nowrap"
        return "<span class='{css_class}'>{unit}</span>".format(
            unit=unit, css_class=css_class)

    def get_category_title(self, analysis):
        """Returns the title of the category the analysis is assigned to
        """
        obj = api.get_object(analysis)
        cat_uid = obj.getRawCategory()
        if not cat_uid:
            return ""
        cat = self.get_object(cat_uid)
        return api.get_title(cat)

    def _folder_item_category(self, analysis_brain, item):
        """Sets the category to the item passed in

        :param analysis_brain: Brain that represents an analysis
        :param item: analysis' dictionary counterpart that represents a row
        """
        if not self.show_categories:
            return

        # get the category title
        cat = self.get_category_title(analysis_brain)

        item["category"] = cat
        cat_order = self.analysis_categories_order.get(cat)
        if (cat, cat_order) not in self.categories:
            self.categories.append((cat, cat_order))

    def _folder_item_css_class(self, analysis_brain, item):
        """Sets the suitable css class name(s) to `table_row_class` from the
        item passed in, depending on the properties of the analysis object

        :param analysis_brain: Brain that represents an analysis
        :param item: analysis' dictionary counterpart that represents a row
        """
        meta_type = analysis_brain.meta_type

        # Default css names for table_row_class
        css_names = item.get('table_row_class', '').split()
        css_names.extend(['state-{}'.format(analysis_brain.review_state),
                          'type-{}'.format(meta_type.lower())])

        if meta_type == 'ReferenceAnalysis':
            css_names.append('qc-analysis')

        elif meta_type == 'DuplicateAnalysis':
            if analysis_brain.getAnalysisPortalType == 'ReferenceAnalysis':
                css_names.append('qc-analysis')

        item['table_row_class'] = ' '.join(css_names)

    def _folder_item_duedate(self, analysis_brain, item):
        """Set the analysis' due date to the item passed in.

        :param analysis_brain: Brain that represents an analysis
        :param item: analysis' dictionary counterpart that represents a row
        """

        # Note that if the analysis is a Reference Analysis, `getDueDate`
        # returns the date when the ReferenceSample expires. If the analysis is
        # a duplicate, `getDueDate` returns the due date of the source analysis
        due_date = analysis_brain.getDueDate
        if not due_date:
            return None
        due_date_str = self.ulocalized_time(due_date, long_format=0)
        item['DueDate'] = due_date_str

        # If the Analysis is late/overdue, display an icon
        capture_date = analysis_brain.getResultCaptureDate
        capture_date = capture_date or DateTime()
        if capture_date > due_date:
            # The analysis is late or overdue
            img = get_image('late.png', title=t(_("Late Analysis")),
                            width='16px', height='16px')
            item['replace']['DueDate'] = '{} {}'.format(due_date_str, img)

    def _folder_item_result(self, analysis_brain, item):
        """Set the analysis' result to the item passed in.

        :param analysis_brain: Brain that represents an analysis
        :param item: analysis' dictionary counterpart that represents a row
        """

        item["Result"] = ""

        if not self.has_permission(ViewResults, analysis_brain):
            # If user has no permissions, don"t display the result but an icon
            img = get_image("to_follow.png", width="16px", height="16px")
            item["before"]["Result"] = img
            return

        # Get the analysis object
        obj = self.get_object(analysis_brain)

        result = obj.getResult()
        capture_date = obj.getResultCaptureDate()
        localized_capture_date = dtime.to_localized_time(
            capture_date, long_format=1)

        item["Result"] = result
        item["ResultCaptureDate"] = dtime.to_iso_format(capture_date)
        item["replace"]["ResultCaptureDate"] = localized_capture_date

        # Add the unit after the result
        unit = item.get("Unit")
        if unit:
            item["after"]["Result"] = self.render_unit(unit)

        # Edit mode enabled of this Analysis
        if self.is_analysis_edition_allowed(analysis_brain):
            # Allow to set Remarks
            item["allow_edit"].append("Remarks")

            if self.is_manual_result_capture_date_allowed():
                # Allow to edit the capture date, e.g. when the result was
                # captured manually after the instrument measurement.
                item["allow_edit"].append("ResultCaptureDate")

            # Set the results field editable
            if self.is_result_edition_allowed(analysis_brain):
                item["allow_edit"].append("Result")

            # Display the DL operand (< or >) in the results entry field if
            # the manual entry of DL is set, but DL selector is hidden
            allow_manual = obj.getAllowManualDetectionLimit()
            selector = obj.getDetectionLimitSelector()
            if allow_manual and not selector:
                operand = obj.getDetectionLimitOperand()
                item["Result"] = "{} {}".format(operand, result).strip()

            # Prepare result options
            result_type = obj.getResultType()
            item["result_type"] = result_type

            choices = self.get_result_options(obj)
            if choices:
                if result_type == "select":
                    # By default set empty as the default selected choice
                    choices.insert(0, dict(ResultValue="", ResultText=""))
                item["choices"]["Result"] = choices

            if result_type == "numeric":
                item["help"]["Result"] = _(
                    "Enter the result either in decimal or scientific "
                    "notation, e.g. 0.00005 or 1e-5, 10000 or 1e5")

        if not result:
            return

        formatted_result = obj.getFormattedResult(
            sciformat=int(self.scinot), decimalmark=self.dmk)
        item["formatted_result"] = formatted_result

    def get_result_options(self, analysis):
        """Returns the result options of the analysis to be rendered or empty
        """
        options = copy(analysis.getResultOptions())
        sort_by = analysis.getResultOptionsSorting()
        if not sort_by:
            return options

        sort_by, sort_order = sort_by.split("-")
        reverse = sort_order == "desc"
        return sorted(options, key=itemgetter(sort_by), reverse=reverse)

    def is_multi_interim(self, interim):
        """Returns whether the interim stores a list of values instead of a
        single value
        """
        result_type = interim.get("result_type", "")
        return result_type.startswith("multi")

    @deprecated("Use api.to_list instead")
    def to_list(self, value):
        """Converts the value to a list
        """
        return api.to_list(value)

    def get_interim_choices(self, interim):
        """Parse the interim choices field
        """
        choices = interim.get("choices")
        if not choices:
            return None
        items = choices.split("|")
        pairs = map(lambda item: item.strip().split(":"), items)
        return OrderedDict(pairs)

    def _normalize_tiered_multivalue(self, interim_field):
        # 标记为 multivalue，让前端按“多值”渲染
        interim_field["result_type"] = "multivalue:tiered"

        # 先拿原始的 options/choices，不要一上来就 strip()
        raw = interim_field.get("options") or interim_field.get("choices") or u""
        # 先转 unicode，再 strip，避免 ascii decode 错误
        raw = _u(raw).strip()

        cfg_rows = None
        cfg_labels = None
        cfg_size = None
        if raw:
            parts = [p.strip() for p in _u(raw).split(u";") if p.strip()]
            for p in parts:
                if u"=" in p:
                    k, v = p.split(u"=", 1)
                    k = _u(k).strip().lower()
                    v = _u(v).strip()
                    if k == "rows":
                        try:
                            cfg_rows = int(v)
                        except Exception:
                            cfg_rows = None
                    elif k == "labels":
                        cfg_labels = v
                    elif k == "size":
                        try:
                            cfg_size = int(v)
                        except Exception:
                            cfg_size = None
                else:
                    if cfg_labels is None:
                        cfg_labels = _u(p)

        # 归一化 value
        val = interim_field.get("value", [])

        # ===== 1) 新格式：{"status": "...", "values": [...]}，保持原样 =====
        if isinstance(val, basestring):
            val_u = _u(val)
            val_s = val_u.lstrip()
            if val_s.startswith(u"{") and u'"status"' in val_s:
                interim_field["value"] = val

                # 这里你原来就给 rows/labels，我保留，因为这是你自定义的 json 结构
                rows = interim_field.get("rows") or 6
                labels = interim_field.get("labels") or [u"#{}".format(i + 1) for i in range(rows)]

                interim_field["rows"] = rows
                interim_field["labels"] = labels
                if cfg_size is not None:
                    interim_field["size"] = cfg_size
                return

        # ===== 2) 分装出来的情况：value 是一个全空的 list，要收成 "" =====
        # 例：["", "", "", "", "", ""] 或 []
        if isinstance(val, (list, tuple)):
            # 判断是不是“全空”
            if not val or all((v is None or _u(v).strip() == u"") for v in val):
                # 这里改了：不再补 rows/labels，直接交给前端
                interim_field["value"] = u""
                if cfg_size is not None:
                    interim_field["size"] = cfg_size
                return

        # ===== 3) 老的列表逻辑（保留你原来的） =====
        if not isinstance(val, (list, tuple)):
            parsed = _json_loads_safe(val)
            if isinstance(parsed, list):
                val = parsed
            else:
                val = [_u(val)] if val else []
        _val = []
        for v in val:
            if v is None:
                _val.append(u"")
            elif isinstance(v, basestring):
                _val.append(_u(v))
            else:
                _val.append(_u(v))
        val = _val

        # 到这里，val 里**可能**有内容，也可能还是全空，但能走到这里说明你不想在上面提前返回
        # 下面继续你的 labels/rows 解析

        # 解析 labels：优先字段里的 labels，否则来自 cfg_labels
        labels = interim_field.get("labels")
        if labels:
            if isinstance(labels, basestring):
                labels_s = _u(labels)
                parsed = _json_loads_safe(labels_s) if labels_s.startswith(u"[") else None
                if isinstance(parsed, list):
                    labels = parsed
                else:
                    sep = u"|" if (u"|" in labels_s) else u","
                    labels = [s.strip() for s in _u(labels_s).split(sep) if s.strip()]
            elif not isinstance(labels, (list, tuple)):
                labels = [_u(labels)]
            else:
                labels = [_u(s) for s in labels]
        else:
            if cfg_labels:
                cfg_labels_s = _u(cfg_labels)
                parsed = _json_loads_safe(cfg_labels_s) if cfg_labels_s.startswith(u"[") else None
                if not isinstance(parsed, list):
                    sep = u"|" if (u"|" in cfg_labels_s) else u","
                    parsed = [s.strip() for s in cfg_labels_s.split(sep) if s.strip()]
                labels = []
                for s in parsed:
                    s = _u(s)
                    if u":" in s:
                        left, right = s.split(u":", 1)
                        labels.append(_u(right).strip())
                    else:
                        labels.append(s)
            else:
                labels = []

        # 行数
        rows = interim_field.get("rows")
        try:
            rows = int(rows) if rows not in (None, "") else None
        except Exception:
            rows = None
        if rows is None:
            rows = cfg_rows if cfg_rows is not None else (len(labels) or 6)
        rows = max(1, int(rows))

        # ===== 新增的关键判断：如果这里还是全空，就别再对齐了，交给前端 =====
        all_empty = (not val) or all((v is None or _u(v).strip() == u"") for v in val)
        if all_empty:
            # 给你保留 rows/labels 的配置吗？你前端已经处理了，就都不塞
            interim_field["value"] = u""
            if cfg_size is not None:
                interim_field["size"] = cfg_size
            return

        # ===== 真正有内容的情况才走下面的对齐 =====
        labels = [_u(x).strip() for x in (labels or [])]
        if not labels:
            labels = [u"#{}".format(i + 1) for i in range(rows)]
        labels = list(labels)[:rows] + [u""] * max(0, rows - len(labels))
        val = list(val)[:rows] + [u""] * max(0, rows - len(val))

        size = interim_field.get("size")
        try:
            size = int(size) if size not in (None, "") else None
        except Exception:
            size = None
        if cfg_size is not None:
            size = cfg_size

        interim_field["value"] = val
        interim_field["rows"] = rows
        interim_field["labels"] = labels
        if size is not None:
            interim_field["size"] = size

    def _normalize_posneg_with_note(self, interim_field):
        """posneg_with_note 显示时不用特别处理，保留前端/数据库里的原样"""
        value = interim_field.get("value", u"") or u""
        # 不拆、不改，只是让前端能用 formatted_value 这个字段
        interim_field["formatted_value"] = value

    def _folder_item_calculation(self, analysis_brain, item):
        """Set the analysis' calculation and interims to the item passed in.

        :param analysis_brain: Brain that represents an analysis
        :param item: analysis' dictionary counterpart that represents a row
        """

        if not self.has_permission(ViewResults, analysis_brain):
            # Hide interims and calculation if user cannot view results
            return

        is_editable = self.is_analysis_edition_allowed(analysis_brain)

        # calculation
        calculation = self.get_calculation(analysis_brain)
        calculation_uid = api.get_uid(calculation) if calculation else ""
        calculation_title = api.get_title(calculation) if calculation else ""
        calculation_link = get_link_for(calculation) if calculation else ""
        item["calculation"] = calculation_uid
        item["Calculation"] = calculation_title
        item["replace"]["Calculation"] = calculation_link or _("Manual")

        if is_editable and calculation:
            url = analysis_brain.getURL()

            item["after"]["Result"] = item["after"].get("Result") or ""
            item["after"]["Result"] += get_link(
                "{}/action/recalculate".format(url),
                value="<i class='small text-secondary fas fa-sync'></i>",
                title=t(_("Recalculate")), css_class="listing-ajax-action")

        # Set interim fields. Note we add the key 'formatted_value' to the list
        # of interims the analysis has already assigned.
        analysis_obj = self.get_object(analysis_brain)
        interim_fields = analysis_obj.getInterimFields() or list()

        # 增加染色靶标过滤：
        targets = get_selected_targets_from_conditions(
            analysis_obj.getConditions(),
            title=u"染色靶标",
        )

        interim_fields = filter_interim_fields_by_targets(
            interim_fields, targets, debug=False,
        )

        # Copy to prevent to avoid persistent changes
        interim_fields = deepcopy(interim_fields)
        for interim_field in interim_fields:
            interim_keyword = interim_field.get("keyword", "")
            if not interim_keyword:
                continue

            # ---------- 规范 tiered_multivalue (病理H-score计算)----------
            rtype = (interim_field.get("result_type") or interim_field.get("type") or "").strip().lower()
            if rtype in ("tiered_multivalue", "multivalue:tiered"):
                self._normalize_tiered_multivalue(interim_field)

            if rtype == 'posneg_with_note':
                self._normalize_posneg_with_note(interim_field)

            interim_value = interim_field.get("value", "")
            interim_allow_empty = interim_field.get("allow_empty") == "on"
            interim_unit = interim_field.get("unit", "")

            # Get the interim's formatted value（必须在归一化之后）
            interim_formatted = self.get_formatted_interim(interim_field)
            interim_field["formatted_value"] = interim_formatted

            # 更新行数据
            item[interim_keyword] = interim_field
            item["class"][interim_keyword] = "interim"

            # 渲染单位
            if interim_unit:
                formatted_interim_unit = format_supsub(interim_unit)
                item["after"][interim_keyword] = self.render_unit(
                    formatted_interim_unit)

            can_edit = self._is_manage_user() or self._is_assigned_to_me(analysis_obj)

            # 权限：是否可编辑
            if is_editable and can_edit:
                if self.has_permission(FieldEditAnalysisResult, analysis_brain):
                    item["allow_edit"].append(interim_keyword)
            else:
                interim_field["value"] = interim_formatted

            # 非隐藏列 → 注入到动态中间字段表头集合
            interim_hidden = interim_field.get("hidden", False)
            if not interim_hidden:
                interim_title = interim_field.get("title")
                self.interim_columns[interim_keyword] = interim_title

            # 下拉/多选 choices
            choices = self.get_interim_choices(interim_field)
            if choices:
                multi = self.is_multi_interim(interim_field)

                # 单值且无默认 → 允许空选项
                if not interim_value and not multi:
                    interim_allow_empty = True

                headers = ["ResultValue", "ResultText"]
                dl = map(lambda it: dict(zip(headers, it)), choices.items())

                if interim_allow_empty:
                    empty = {"ResultValue": "", "ResultText": ""}
                    dl = [empty] + list(dl)

                item.setdefault("choices", {})[interim_keyword] = dl

            # if not is_editable:
            #     # 只读时显示格式化文本
            #     interim_field["value"] = interim_formatted
            if not is_editable:

                rtype = (interim_field.get("result_type") or "").strip().lower()
                if rtype == "tick":
                    interim_field["value"] = interim_formatted
                else:
                    interim_field["value"] = interim_formatted

            item[interim_keyword] = interim_field

        item["interimfields"] = interim_fields
        self.interim_fields[analysis_brain.UID] = interim_fields

    def get_formatted_interim(self, interim):
        """Returns the formatted value of the interim
        """
        rtype = (interim.get("result_type") or u"").strip().lower()
        raw_value = interim.get("value")

        # 把三态JSON
        if isinstance(raw_value, basestring):
            raw_u = _u(raw_value)
            raw_strip = raw_u.lstrip()
            if raw_strip.startswith(u"{") and u'"status"' in raw_strip:
                # 这种就不要动，前段自己解析
                return raw_value

        if rtype in (u"multivalue:tiered", u"tiered_multivalue", u"tiered-multivalue"):
            raw_value = interim.get("value") or u""
            return raw_value
        if rtype == "tick":
            # 统一转成 True/False boolean，让前端正确回显
            if isinstance(raw_value, bool):
                return raw_value
            if isinstance(raw_value, basestring):
                return raw_value.lower() in ("true", "on", "1", "yes")
            return bool(raw_value)
            # 阳/阴/未检测+备注
        if rtype == u"posneg_with_note":
            raw = (raw_value or u"").strip()
            if raw.startswith(u"A|"):
                note = raw.split(u"|", 1)[1]
                return u"阳性: {}".format(safe_unicode(note))
            elif raw == u"A":
                return u"阳性"
            elif raw == u"B":
                return u"阴性"
            elif raw == u"C":
                return u"未检测"
            else:
                return safe_unicode(raw)

        # get the 'raw' value stored for this interimi
        raw_value = interim.get("value")

        if self.is_multi_interim(interim):
            # value is a jsonified list of values
            values = api.to_list(raw_value)
        else:
            values = [raw_value]

        # remove empties
        values = filter(None, values)

        choices = self.get_interim_choices(interim)
        if choices:
            # values are predefined options for selection
            values = [choices.get(v) for v in values]
        else:
            # values are captured directly by the user
            values = [formatDecimalMark(value, self.dmk) for value in values]

        # return the values as a single string
        values = filter(None, values)
        return "<br/>".join(values)

    def _folder_item_unit(self, analysis_brain, item):
        """Fills the analysis' unit to the item passed in.

        :param analysis_brain: Brain that represents an analysis
        :param item: analysis' dictionary counterpart that represents a row
        """
        if not self.is_analysis_edition_allowed(analysis_brain):
            return

        # Edition allowed
        voc = self.get_unit_vocabulary(analysis_brain)
        if voc:
            item["choices"]["Unit"] = voc
            item["allow_edit"].append("Unit")

    def _folder_item_method(self, analysis_brain, item):
        """Fills the analysis' method to the item passed in.

        :param analysis_brain: Brain that represents an analysis
        :param item: analysis' dictionary counterpart that represents a row
        """
        obj = self.get_object(analysis_brain)
        is_editable = self.is_analysis_edition_allowed(analysis_brain)

        # append new
        assigned = getattr(obj, "getAnalyst", lambda: "")()
        assigned_id = getattr(assigned, "getId", lambda: assigned)() or ""
        current_id = getattr(api.get_current_user(), "getId", lambda: "")()
        if not assigned_id or assigned_id != current_id:
            is_editable = False

        # is_editable = self.is_analysis_edition_allowed(analysis_brain)
        # can_edit = self._is_manager_user() or self._is_assigned_to_me(analysis_obj)
        # if not can_edit:
        #     is_editable = False

        if is_editable:
            method_vocabulary = self.get_methods_vocabulary(analysis_brain)
            item["Method"] = obj.getRawMethod()  # 多选可以直接是 list
            item["choices"]["Method"] = method_vocabulary
            item["allow_edit"].append("Method")
        else:
            methods = obj.getMethod()

            # 兼容单值或多值
            if not isinstance(methods, (list, tuple)):
                methods = [methods] if methods else []

            # 拼接标题文本
            method_titles = [api.get_title(m) for m in methods if m]
            item["Method"] = ", ".join(method_titles)

            # 拼接链接按钮
            links = [get_link_for(m, tabindex="-1") for m in methods if m]
            item["replace"]["Method"] = "<br/>".join(links)

    def _on_method_change(self, uid=None, value=None, item=None, **kw):
        """Update instrument and calculation when the method changes

        :param uid: object UID
        :value: UID(s) of the new method, string or list
        :item: old folderitem

        :returns: updated folderitem
        """

        obj = api.get_object_by_uid(uid, None)

        # 统一处理多选和单选
        uids = []
        if isinstance(value, list):
            uids = value
        elif isinstance(value, str):
            uids = value.split(",") if value else []
        # logger.info(" [_on_method_change] 多选解析后的 uids = %s", uids)

        methods = [api.get_object_by_uid(uid) for uid in uids if uid]
        methods = [m for m in methods if m is not None]

        if not all([obj, methods, item]):
            logger.warning("[_on_method_change] 参数不完整: obj=%s, methods=%s, item=%s", obj, methods, item)
            return None

        # 选择第一个 method 获取 instrument vocab
        method = methods[0] if methods else None
        if method is not None:
            inst_vocab = self.get_instruments_vocabulary(obj, method=method)
            item["choices"]["Instrument"] = inst_vocab
            # logger.info("[_on_method_change] 更新 Instrument choices: %s", inst_vocab)
        else:
            logger.warning("[_on_method_change] 没有找到有效的 method")

        return item

    def _folder_item_instrument(self, analysis_brain, item):
        """Fills the analysis' instrument to the item passed in.

        :param analysis_brain: Brain that represents an analysis
        :param item: analysis' dictionary counterpart that represents a row
        """
        item["Instrument"] = ""

        # Instrument can be assigned to this analysis
        is_editable = self.is_analysis_edition_allowed(analysis_brain)
        instrument = self.get_instrument(analysis_brain)

        if is_editable:
            # Edition allowed
            voc = self.get_instruments_vocabulary(analysis_brain)
            item["Instrument"] = instrument.UID() if instrument else ""
            item["choices"]["Instrument"] = voc
            item["allow_edit"].append("Instrument")

        elif instrument:
            # Edition not allowed
            item["Instrument"] = api.get_title(instrument)
            instrument_link = get_link_for(instrument, tabindex="-1")
            item["replace"]["Instrument"] = instrument_link

        else:
            item["Instrument"] = _("Manual")

    def _on_unit_change(self, uid=None, value=None, item=None, **kw):
        """ updates the rendered unit on selection of unit.
        """
        if not all([value, item]):
            return None
        item["after"]["Result"] = self.render_unit(value)
        uncertainty = item.get("Uncertainty")
        if uncertainty:
            item["after"]["Uncertainty"] = self.render_unit(value)
        elif "Uncertainty" in item["allow_edit"]:
            item["after"]["Uncertainty"] = self.render_unit(value)
        return item

    # def _folder_item_analyst(self, obj, item):
    #     obj = self.get_object(obj)
    #     analyst = obj.getAnalyst()
    #     item["Analyst"] = self.get_user_name(analyst)

    def _folder_item_submitted_by(self, obj, item):
        obj = self.get_object(obj)
        submitted_by = obj.getSubmittedBy()
        item["SubmittedBy"] = self.get_user_name(submitted_by)

    @viewcache.memoize
    def get_user_name(self, user_id):
        if not user_id:
            return ""
        user = api.get_user_properties(user_id)
        return user and user.get("fullname") or user_id

    def _folder_item_attachments(self, obj, item):
        if not self.has_permission(ViewResults, obj):
            return

        attachments_names = []
        attachments_html = []
        analysis = self.get_object(obj)
        for attachment in analysis.getRawAttachment():
            attachment = self.get_object(attachment)
            link = self.get_attachment_link(attachment)
            attachments_html.append(link)
            filename = attachment.getFilename()
            attachments_names.append(filename)

        if attachments_html:
            item["replace"]["Attachments"] = "<br/>".join(attachments_html)
            item["Attachments"] = ", ".join(attachments_names)

        elif analysis.getAttachmentRequired():
            img = get_image("warning.png", title=_("Attachment required"))
            item["replace"]["Attachments"] = img

    def get_attachment_link(self, attachment):
        """Returns a well-formed link for the attachment passed in
        """
        filename = attachment.getFilename()
        att_url = api.get_url(attachment)
        url = "{}/at_download/AttachmentFile".format(att_url)
        return get_link(url, filename, tabindex="-1")

    def _folder_item_uncertainty(self, analysis_brain, item):
        """Fills the analysis' uncertainty to the item passed in.

        :param analysis_brain: Brain that represents an analysis
        :param item: analysis' dictionary counterpart that represents a row
        """
        item["Uncertainty"] = ""

        if not self.has_permission(ViewResults, analysis_brain):
            return

        # Wake up the Analysis object
        obj = self.get_object(analysis_brain)

        # NOTE: When we allow to edit the uncertainty, we want to have the raw
        #       uncertainty value and not the formatted (and possibly rounded)!
        #       This ensures that not the rounded value get stored
        allow_edit = self.is_uncertainty_edition_allowed(analysis_brain)
        if allow_edit:
            item["Uncertainty"] = obj.getUncertainty()
            item["before"]["Uncertainty"] = "± "
            item["allow_edit"].append("Uncertainty")
            unit = item.get("Unit")
            if unit:
                item["after"]["Uncertainty"] = self.render_unit(unit)
            return

        formatted = format_uncertainty(
            obj, decimalmark=self.dmk, sciformat=int(self.scinot))
        if formatted:
            item["replace"]["Uncertainty"] = formatted
            item["before"]["Uncertainty"] = "± "
            unit = item.get("Unit")
            if unit:
                item["after"]["Uncertainty"] = self.render_unit(unit)

    def _folder_item_detection_limits(self, analysis_brain, item):
        """Fills the analysis' detection limits to the item passed in.

        :param analysis_brain: Brain that represents an analysis
        :param item: analysis' dictionary counterpart that represents a row
        """
        item["DetectionLimitOperand"] = ""

        if not self.is_analysis_edition_allowed(analysis_brain):
            # Return immediately if the we are not in edit mode
            return

        # TODO: Performance, we wake-up the full object here
        obj = self.get_object(analysis_brain)

        # No Detection Limit Selection
        if not obj.getDetectionLimitSelector():
            return None

        # Show Detection Limit Operand Selector
        item["DetectionLimitOperand"] = obj.getDetectionLimitOperand()
        item["allow_edit"].append("DetectionLimitOperand")
        self.columns["DetectionLimitOperand"]["toggle"] = True

        # Prepare selection list for LDL/UDL
        choices = [
            {"ResultValue": "", "ResultText": ""},
            {"ResultValue": LDL, "ResultText": LDL},
            {"ResultValue": UDL, "ResultText": UDL}
        ]
        # Set the choices to the item
        item["choices"]["DetectionLimitOperand"] = choices

    def _folder_item_specifications(self, analysis_brain, item):
        """Set the results range to the item passed in"""
        analysis = self.get_object(analysis_brain)
        results_range = analysis.getResultsRange()

        # get the results range interval properly formatted
        value = get_formatted_interval(results_range, "")

        # for non-floatable analyses, display the comment instead
        result_type = analysis.getResultType()
        if result_type not in ["numeric", "string"]:
            comment = results_range.get("rangecomment")
            value = comment if comment else value

        item["Specification"] = value

    def _folder_item_out_of_range(self, analysis_brain, item):
        """Displays an icon if result is out of range
        """
        if not self.has_permission(ViewResults, analysis_brain):
            # Users without permissions to see the result should not be able
            # to see if the result is out of range naither
            return

        analysis = self.get_object(analysis_brain)
        out_range, out_shoulders = is_out_of_range(analysis)
        if out_range:
            msg = _("Result out of range")
            img = get_image("exclamation.png", title=msg)
            if not out_shoulders:
                msg = _("Result in shoulder range")
                img = get_image("warning.png", title=msg)
            self._append_html_element(item, "Result", img)

    def _folder_item_result_range_compliance(self, analysis_brain, item):
        """Displays an icon if the range is different from the results ranges
        defined in the Sample
        """
        if not IAnalysisRequest.providedBy(self.context):
            return

        analysis = self.get_object(analysis_brain)
        if is_result_range_compliant(analysis):
            return

        # Non-compliant range, display an icon
        service_uid = analysis_brain.getServiceUID
        original = self.context.getResultsRange(search_by=service_uid)
        original = get_formatted_interval(original, "")
        msg = _("Result range is different from Specification: {}"
                .format(original))
        img = get_image("warning.png", title=msg)
        self._append_html_element(item, "Specification", img)

    def _folder_item_verify_icons(self, analysis_brain, item):
        """Set the analysis' verification icons to the item passed in.

        :param analysis_brain: Brain that represents an analysis
        :param item: analysis' dictionary counterpart that represents a row
        """
        submitter = analysis_brain.getSubmittedBy
        if not submitter:
            # This analysis hasn't yet been submitted, no verification yet
            return

        if analysis_brain.review_state == 'retracted':
            # Don't display icons and additional info about verification
            return

        verifiers = analysis_brain.getVerificators
        in_verifiers = submitter in verifiers
        if in_verifiers:
            # If analysis has been submitted and verified by the same person,
            # display a warning icon
            msg = t(_("Submitted and verified by the same user: {}"))
            icon = get_image('warning.png', title=msg.format(submitter))
            self._append_html_element(item, 'state_title', icon)

        num_verifications = analysis_brain.getNumberOfRequiredVerifications
        if num_verifications > 1:
            # More than one verification required, place an icon and display
            # the number of verifications done vs. total required
            done = analysis_brain.getNumberOfVerifications
            pending = num_verifications - done
            ratio = float(done) / float(num_verifications) if done > 0 else 0
            ratio = int(ratio * 100)
            scale = ratio == 0 and 0 or (ratio / 25) * 25
            anchor = "<a href='#' tabindex='-1' title='{} &#13;{} {}' " \
                     "class='multi-verification scale-{}'>{}/{}</a>"
            anchor = anchor.format(t(_("Multi-verification required")),
                                   str(pending),
                                   t(_("verification(s) pending")),
                                   str(scale),
                                   str(done),
                                   str(num_verifications))
            self._append_html_element(item, 'state_title', anchor)

        if analysis_brain.review_state != 'to_be_verified':
            # The verification of analysis has already been done or first
            # verification has not been done yet. Nothing to do
            return

        # Check if the user has "Bika: Verify" privileges
        if not self.has_permission(TransitionVerify):
            # User cannot verify, do nothing
            return

        username = api.get_current_user().id
        if username not in verifiers:
            # Current user has not verified this analysis
            if submitter != username:
                # Current user is neither a submitter nor a verifier
                return

            # Current user is the same who submitted the result
            if analysis_brain.isSelfVerificationEnabled:
                # Same user who submitted can verify
                title = t(_("Can verify, but submitted by current user"))
                html = get_image('warning.png', title=title)
                self._append_html_element(item, 'state_title', html)
                return

            # User who submitted cannot verify
            title = t(_("Cannot verify, submitted by current user"))
            html = get_image('submitted-by-current-user.png', title=title)
            self._append_html_element(item, 'state_title', html)
            return

        # This user verified this analysis before
        multi_verif = self.context.bika_setup.getTypeOfmultiVerification()
        if multi_verif != 'self_multi_not_cons':
            # Multi verification by same user is not allowed
            title = t(_("Cannot verify, was verified by current user"))
            html = get_image('submitted-by-current-user.png', title=title)
            self._append_html_element(item, 'state_title', html)
            return

        # Multi-verification by same user, but non-consecutively, is allowed
        if analysis_brain.getLastVerificator != username:
            # Current user was not the last user to verify
            title = t(
                _("Can verify, but was already verified by current user"))
            html = get_image('warning.png', title=title)
            self._append_html_element(item, 'state_title', html)
            return

        # Last user who verified is the same as current user
        title = t(_("Cannot verify, last verified by current user"))
        html = get_image('submitted-by-current-user.png', title=title)
        self._append_html_element(item, 'state_title', html)
        return

    def _folder_item_assigned_worksheet(self, analysis_brain, item):
        """Adds an icon to the item dict if the analysis is assigned to a
        worksheet and if the icon is suitable for the current context

        :param analysis_brain: Brain that represents an analysis
        :param item: analysis' dictionary counterpart that represents a row
        """
        if not IAnalysisRequest.providedBy(self.context):
            # We want this icon to only appear if the context is an AR
            return

        analysis_obj = self.get_object(analysis_brain)
        worksheet = analysis_obj.getWorksheet()
        if not worksheet:
            # No worksheet assigned. Do nothing
            return

        title = t(_("Assigned to: ${worksheet_id}",
                    mapping={'worksheet_id': safe_unicode(worksheet.id)}))
        img = get_image('worksheet.png', title=title)
        anchor = get_link(worksheet.absolute_url(), img, tabindex="-1")
        self._append_html_element(item, 'state_title', anchor)

    def _folder_item_accredited_icon(self, analysis_brain, item):
        """Adds an icon to the item dictionary if it is an accredited analysis
        """
        full_obj = self.get_object(analysis_brain)
        if full_obj.getAccredited():
            img = get_image("accredited.png", title=t(_("Accredited")))
            self._append_html_element(item, "Service", img)

    def _folder_item_partition(self, analysis_brain, item):
        """Adds an anchor to the partition if the current analysis is from a
        partition that does not match with the current context
        """
        if not IAnalysisRequest.providedBy(self.context):
            return

        sample_id = analysis_brain.getRequestID
        if sample_id != api.get_id(self.context):
            if not self.show_partitions:
                # Do not display the link
                return

            part_url = analysis_brain.getRequestURL
            kwargs = {"class": "small", "tabindex": "-1"}
            url = get_link(part_url, value=sample_id, **kwargs)
            title = item["replace"].get("Service") or item["Service"]
            item["replace"]["Service"] = "{}<br/>{}".format(title, url)

    def _folder_item_report_visibility(self, analysis_brain, item):
        """Set if the hidden field can be edited (enabled/disabled)

        :analysis_brain: Brain that represents an analysis
        :item: analysis' dictionary counterpart to be represented as a row"""
        # Users that can Add Analyses to an Analysis Request must be able to
        # set the visibility of the analysis in results report, also if the
        # current state of the Analysis Request (e.g. verified) does not allow
        # the edition of other fields. Note that an analyst has no privileges
        # by default to edit this value, cause this "visibility" field is
        # related with results reporting and/or visibility from the client
        # side. This behavior only applies to routine analyses, the visibility
        # of QC analyses is managed in publish and are not visible to clients.
        if 'Hidden' not in self.columns:
            return

        full_obj = self.get_object(analysis_brain)
        item['Hidden'] = full_obj.getHidden()

        # Hidden checkbox is not reachable by tabbing
        item["tabindex"]["Hidden"] = "disabled"
        if self.has_permission(FieldEditAnalysisHidden, obj=full_obj):
            item['allow_edit'].append('Hidden')

    def _folder_item_fieldicons(self, analysis_brain):
        """Resolves if field-specific icons must be displayed for the object
        passed in.

        :param analysis_brain: Brain that represents an analysis
        """
        full_obj = self.get_object(analysis_brain)
        uid = api.get_uid(full_obj)
        for name, adapter in getAdapters((full_obj,), IFieldIcons):
            alerts = adapter()
            if not alerts or uid not in alerts:
                continue
            alerts = alerts[uid]
            if uid not in self.field_icons:
                self.field_icons[uid] = alerts
                continue
            self.field_icons[uid].extend(alerts)

    def _folder_item_remarks(self, analysis_brain, item):
        """Renders the Remarks field for the passed in analysis

        If the edition of the analysis is permitted, adds the field into the
        list of editable fields.

        :param analysis_brain: Brain that represents an analysis
        :param item: analysis' dictionary counterpart that represents a row
        """

        if self.analysis_remarks_enabled():
            item["Remarks"] = analysis_brain.getRemarks

        if self.is_analysis_edition_allowed(analysis_brain):
            item["allow_edit"].extend(["Remarks"])
        else:
            # render HTMLified text in readonly mode
            item["Remarks"] = api.text_to_html(
                analysis_brain.getRemarks, wrap=None)

    def _append_html_element(self, item, element, html, glue="&nbsp;",
                             after=True):
        """Appends an html value after or before the element in the item dict

        :param item: dictionary that represents an analysis row
        :param element: id of the element the html must be added thereafter
        :param html: element to append
        :param glue: glue to use for appending
        :param after: if the html content must be added after or before"""
        position = after and 'after' or 'before'
        item[position] = item.get(position, {})
        original = item[position].get(element, '')
        if not original:
            item[position][element] = html
            return
        item[position][element] = glue.join([original, html])

    def _folder_item_conditions(self, analysis_brain, item):
        """Renders the analysis conditionsG
        """
        analysis = self.get_object(analysis_brain)

        if not IRoutineAnalysis.providedBy(analysis):  # 如果分析对象不是常规分析，则返回
            return

        conditions = analysis.getConditions()  # 获取分析的条件
        if not conditions:
            return

        def to_str(condition):
            title = condition.get("title")  # 获取条件标题
            value = condition.get("value", "")  # 获取条件的值
            if condition.get("type") == "file" and api.is_uid(value):  # 如果条件是文件，则附上文件链接
                att = self.get_object(value)
                value = self.get_attachment_link(att)
            return ": ".join([title, str(value)])

        # Display the conditions properly formatted
        conditions = "<br/>".join([to_str(cond) for cond in conditions])
        service = item["replace"].get("Service") or item["Service"]  # 获取服务信息
        item["replace"]["Service"] = "<br/>".join([service, conditions])  # 将条件添加到服务信息之前显示

    def _folder_item_holding_time(self, analysis_brain, item):
        """Adds an icon to the item dictionary if no result has been submitted
        for the analysis and the holding time has passed or is about to expire.
        It also displays the icon if the result was recorded after the holding
        time limit.
        """
        analysis = self.get_object(analysis_brain)
        if not IRoutineAnalysis.providedBy(analysis):
            return

        # get the maximum holding time for this analysis
        max_holding_time = analysis.getMaxHoldingTime()
        if not max_holding_time:
            return

        # get the datetime from which the max holding time is computed
        start_date = analysis.getDateSampled()
        start_date = dtime.to_dt(start_date)
        if not start_date:
            return

        # get the timezone of the start date for correct comparisons
        timezone = dtime.get_timezone(start_date)

        # calculate the maximum holding date
        delta = timedelta(minutes=api.to_minutes(**max_holding_time))
        max_holding_date = dtime.to_ansi(start_date + delta)

        # maybe the result was captured past the holding time
        if ISubmitted.providedBy(analysis):
            captured = analysis.getResultCaptureDate()
            captured = dtime.to_ansi(captured, timezone=timezone)
            if captured > max_holding_date:
                msg = _("The result was captured past the holding time limit.")
                icon = get_fas_ico("exclamation-triangle",
                                   css_class="text-danger",
                                   title=t(msg))
                self._append_html_element(item, "ResultCaptureDate", icon)
            return

        # not yet submitted, maybe the holding time expired
        now = dtime.to_ansi(dtime.now(), timezone=timezone)
        if now > max_holding_date:
            msg = _("The holding time for this sample and analysis has "
                    "expired. Proceeding with the analysis may compromise the "
                    "reliability of the results.")
            icon = get_fas_ico("exclamation-triangle",
                               css_class="text-danger",
                               title=t(msg))
            self._append_html_element(item, "ResultCaptureDate", icon)
            return

        # or maybe is about to expire
        soon = dtime.to_ansi(dtime.now(), timezone=timezone)
        if soon > max_holding_date:
            msg = _("The holding time for this sample and analysis is about "
                    "to expire. Please complete the analysis as soon as "
                    "possible to ensure data accuracy and reliability.")
            icon = get_fas_ico("exclamation-triangle",
                               css_class="text-warning",
                               title=t(msg))
            self._append_html_element(item, "ResultCaptureDate", icon)
            return

    def is_method_required(self, analysis):
        """Returns whether the render of the selection list with methods is
        required for the method passed-in, even if only option "None" is
        displayed for selection
        """
        # Always return true if the analysis has a method assigned
        obj = self.get_object(analysis)
        method = obj.getRawMethod()
        if method:
            return True

        methods = obj.getRawAllowedMethods()
        return len(methods) > 0

    def is_instrument_required(self, analysis):
        """Returns whether the render of the selection list with instruments is
        required for the analysis passed-in, even if only option "None" is
        displayed for selection.
        :param analysis: Brain or object that represents an analysis
        """
        # If method selection list is required, the instrument selection too
        if self.is_method_required(analysis):
            return True

        # Always return true if the analysis has an instrument assigned
        analysis = self.get_object(analysis)
        if analysis.getRawInstrument():
            return True

        instruments = analysis.getRawAllowedInstruments()
        # There is no need to check for the instruments of the method assigned
        # to # the analysis (if any), because the instruments rendered in the
        # selection list are always a subset of the allowed instruments when
        # a method is selected
        return len(instruments) > 0

    def is_unit_choices_required(self, analysis):
        """Returns whether the render of the unit choice selection list is
        required for the analysis passed-in.
        :param analysis: Brain or object that represents an analysis
        """
        # Always return true if the analysis has unitchoices
        analysis = self.get_object(analysis)
        if analysis.getUnitChoices():
            return True
        return False

    def is_method_column_required(self, items):
        """Returns whether the method column has to be rendered or not.
        Returns True if at least one of the analyses from the listing requires
        the list for method selection to be rendered
        """
        for item in items:
            obj = item.get("obj")
            if self.is_method_required(obj):
                return True
        return False

    def is_instrument_column_required(self, items):
        """Returns whether the instrument column has to be rendered or not.
        Returns True if at least one of the analyses from the listing requires
        the list for instrument selection to be rendered
        """
        for item in items:
            obj = item.get("obj")
            if self.is_instrument_required(obj):
                return True
        return False

    def is_unit_selection_column_required(self, items):
        """Returns whether the unit column has to be rendered or not.
        Returns True if at least one of the analyses from the listing requires
        the list for unit selection to be rendered
        """
        for item in items:
            obj = item.get("obj")
            if self.is_unit_choices_required(obj):
                return True
        return False

    def _get_analyst_vocabulary(self, obj=None):
        mtool = getToolByName(self.context, "portal_membership")
        vocab = [{"ResultValue": "", "ResultText": _("None")}]

        # ---------- 内部工具：把 Department 各种写法统一成 set() ----------
        def _normalize_departments(value):
            depts = set()
            if not value:
                return depts

            if hasattr(value, "Title"):
                try:
                    depts.add(_u(value.Title()))
                except Exception:
                    depts.add(_u(value.Title))
                return depts

            # 是列表/元组
            if isinstance(value, (list, tuple)):
                for v in value:
                    depts |= _normalize_departments(v)
                return depts

            s = _u(value)
            s = s.replace(u"；", u";")
            s = s.replace(u";", u",")
            for part in s.split(u","):
                part = part.strip()
                if part:
                    depts.add(part)
            return depts

        # ---------- 找出这条分析所属服务”的部门 ----------
        required_depts = set()
        if obj is not None:
            try:
                analysis = self.get_object(obj)
            except Exception:
                analysis = None

            if analysis is not None:
                service_uid = getattr(analysis, "getServiceUID", lambda: "")()
                if service_uid:
                    service = api.get_object_by_uid(service_uid, None)
                    if service is not None:
                        dept_val = getattr(service, "getDepartment", lambda: "")()
                        required_depts = _normalize_departments(dept_val)

        # ---------- 遍历所有成员，过滤规则 ----------
        candidates = []
        for member in (mtool.listMembers() or []):
            uid = member.getId()
            roles = set(member.getRoles() or [])

            # 必须是 Analyst
            if "Analyst" not in roles:
                continue

            # 用户必须启用
            if member.hasProperty("enabled") and not member.getProperty("enabled"):
                continue
            if member.hasProperty("disabled") and member.getProperty("disabled"):
                continue
            if member.hasProperty("inactive") and member.getProperty("inactive"):
                continue

            # 必须有激活的 LabContact
            contact = get_current_contact(self.context, user_id=uid)
            if not contact:
                continue
            if not api.is_active(contact):
                continue

            # 如果当前分析限制了部门，就只要部门有交集的联系人
            if required_depts:
                contact_depts = _normalize_departments(
                    getattr(contact, "getDepartment", lambda: "")()
                )
                if not (contact_depts & required_depts):
                    # 没交集，丢掉
                    continue

            candidates.append(uid)

        for uid in sorted(set(candidates)):
            props = api.get_user_properties(uid) or {}
            text = props.get("fullname") or uid
            vocab.append({"ResultValue": uid, "ResultText": text})

        return vocab

    def _folder_item_analyst(self, obj, item):
        analysis = self.get_object(obj)
        analyst_id = ""
        try:
            raw = getattr(analysis, "getAnalyst", lambda: "")()
            if raw:
                analyst_id = getattr(raw, "getId", lambda: str(raw))()
        except Exception as e:
            logger.exception("[_folder_item_analyst] getAnalyst() error: %s", e)
            analyst_id = ""

        item["Analyst"] = analyst_id

        item.setdefault("choices", {})
        item.setdefault("allow_edit", [])
        item.setdefault("replace", {})
        # -------- 若“当前用户就是被分配的检验员”，强制只读并返回 --------
        cu = api.get_current_user()
        current_id = getattr(cu, "getId", lambda: None)() or \
                     getattr(cu, "getUserName", lambda: "")()
        if analyst_id and current_id == analyst_id:
            props = api.get_user_properties(analyst_id) or {}
            display = props.get("fullname") or analyst_id
            item["replace"]["Analyst"] = display
            if "Analyst" in item["allow_edit"]:
                item["allow_edit"].remove("Analyst")
            item["choices"].pop("Analyst", None)
            return

        # -------- 非本人：先看分析是否允许编辑 --------
        try:
            editable = self.is_analysis_edition_allowed(obj)
        except Exception:
            editable = False

        if not editable:
            # —— 新增：未分配/登记状态，放开“检验员”下拉 —— #
            wf = getToolByName(self.context, "portal_workflow")
            state = wf.getInfoFor(analysis, "review_state", "") or ""
            roles = set(cu.getRoles() or [])

            if state in ("unassigned", "registered"):
                # 管理类可以分配任意人
                if {"Manager", "LabManager", "Publisher"} & roles:
                    try:
                        vocab = self._get_analyst_vocabulary(obj)
                    except Exception as e:
                        logger.exception("[_folder_item_analyst] _get_analyst_vocabulary() error: %s", e)
                        vocab = [{"ResultValue": "", "ResultText": _("None")}]

                # 普通 Analyst：给“别人”的列表（含 None），排除自己
                elif "Analyst" in roles:
                    try:
                        vocab = self._get_analyst_vocabulary(obj)
                        vocab = [d for d in vocab if d.get("ResultValue") != current_id]
                    except Exception:
                        vocab = [{"ResultValue": "", "ResultText": _("None")}]
                else:
                    vocab = []

                if vocab:
                    # 兜底：确保当前值能回显
                    if analyst_id and all(d.get("ResultValue") != analyst_id for d in vocab):
                        props = api.get_user_properties(analyst_id) or {}
                        name = props.get("fullname") or analyst_id
                        vocab = list(vocab) + [{"ResultValue": analyst_id, "ResultText": name}]
                    item["choices"]["Analyst"] = vocab
                    if "Analyst" not in item["allow_edit"]:
                        item["allow_edit"].append("Analyst")
                    return

            # 只读：显示文本
            display = ""
            if analyst_id:
                props = api.get_user_properties(analyst_id) or {}
                display = props.get("fullname") or analyst_id
            item["replace"]["Analyst"] = display
            return

        # -------- 可编辑：根据当前用户权限决定 choices --------
        roles = set(cu.getRoles() or [])
        can_assign_anyone = bool({"Manager", "LabManager", "Publisher"} & roles)
        can_assign_self = "Analyst" in roles

        if can_assign_anyone:
            try:
                vocab = self._get_analyst_vocabulary(obj)
            except Exception as e:
                logger.exception("[_folder_item_analyst] _get_analyst_vocabulary() error: %s", e)
                vocab = [{"ResultValue": "", "ResultText": _("None")}]
            # 兜底：确保当前值在下拉里（即使被 vocab 过滤）
            if analyst_id and all(d.get("ResultValue") != analyst_id for d in vocab):
                props = api.get_user_properties(analyst_id) or {}
                name = props.get("fullname") or analyst_id
                vocab.append({"ResultValue": analyst_id, "ResultText": name})
            item["choices"]["Analyst"] = vocab
            if "Analyst" not in item["allow_edit"]:
                item["allow_edit"].append("Analyst")
            return

        if can_assign_self:
            try:
                vocab = self._get_analyst_vocabulary(obj)
                vocab = [d for d in vocab if d.get("ResultValue") != current_id]
            except Exception:
                vocab = [{"ResultValue": "", "ResultText": _("None")}]

            # 如果当前值不在 vocab，补进去以便正确回显
            if analyst_id and all(d.get("ResultValue") != analyst_id for d in vocab):
                props = api.get_user_properties(analyst_id) or {}
                name = props.get("fullname") or analyst_id
                vocab.append({"ResultValue": analyst_id, "ResultText": name})

            item["choices"]["Analyst"] = vocab
            if "Analyst" not in item["allow_edit"]:
                item["allow_edit"].append("Analyst")
            return

        # -------- 无权分配：只读文本 --------
        display = ""
        if analyst_id:
            props = api.get_user_properties(analyst_id) or {}
            display = props.get("fullname") or analyst_id
        item["replace"]["Analyst"] = display

    def _on_analyst_change(self, uid=None, value=None, item=None, **kw):
        """Analyst 下拉变更：立即保存到对象，并返回更新后的行"""
        if not uid:
            return None

        obj = api.get_object_by_uid(uid, None)
        if obj is None:
            return None

        # 权限判断
        # if not self.is_analysis_edition_allowed(obj):
        #     return None
        editable = self.is_analysis_edition_allowed(obj)
        if not editable:
            roles = set((api.get_current_user() or {}).getRoles() or [])
            wf = getToolByName(self.context, "portal_workflow")
            state = wf.getInfoFor(obj, "review_state", "") or ""
            # 仅当“未分配/登记”时放行（Analyst 可自选自己；管理类可分配任意人）
            if not (state in ("unassigned", "registered")
                    and ({"Manager", "LabManager", "Publisher"} & roles or "Analyst" in roles)):
                return None

        cu = api.get_current_user()
        current_id = getattr(cu, "getUserName", None)
        current_id = current_id() if callable(current_id) else getattr(cu, "getId", lambda: "")()

        assigned = getattr(obj, "getAnalyst", lambda: "")() or ""
        # new_value = value or ""
        new_value = (value or "").strip()

        roles = set((api.get_current_user() or {}).getRoles() or [])
        managerish = {"Manager", "LabManager", "Publisher"} & roles

        if "Analyst" in roles and not managerish:
            # 普通 Analyst：只要是有效候选（且通常前端已排除自己），就允许
            try:
                allowed_ids = {
                    d.get("ResultValue")
                    for d in self._get_analyst_vocabulary(obj)
                    if d.get("ResultValue")
                }
            except Exception:
                allowed_ids = set()

            # new_value 允许为空(None)或在候选里
            if new_value and new_value not in allowed_ids:
                return None

        if assigned and current_id == assigned and new_value != assigned:
            return None

        analyst_id = new_value or None

        try:
            obj.setAnalyst(analyst_id)
            try:
                obj.reindexObject(idxs=["getAnalyst"])
            except Exception:
                obj.reindexObject()
        except Exception as e:
            logger.exception("[_on_analyst_change] setAnalyst failed: %s", e)
            return None

        # 返回给前端用于“就地更新”这一行
        item = item or {"allow_edit": [], "choices": {}}
        item["Analyst"] = analyst_id

        # 兜底：若当前值不在候选里，补进去
        vocab = item["choices"].get("Analyst", [])
        if analyst_id and all(d.get("ResultValue") != analyst_id for d in vocab):
            props = api.get_user_properties(analyst_id) or {}
            name = props.get("fullname") or analyst_id
            vocab = list(vocab) + [{"ResultValue": analyst_id, "ResultText": name}]
            item["choices"]["Analyst"] = vocab

        return item

    def set_fields(self, data=None):
        """在真正保存之前，把 posneg_with_note 的值放进去"""
        # 1. 先把前端传来的数据拿到
        data = data or self.request.json_body

        # 2. 一个个看
        for field in data:
            name = field.get("name")
            value = field.get("value")

            # 只处理你这几个“中间结果”的字段
            if not name:
                continue
            if not name.startswith("result_"):
                continue
            if not value:
                continue

            if "|" in value:
                field["value"] = value.strip()
        return super(AnalysesView, self).set_fields(data)
