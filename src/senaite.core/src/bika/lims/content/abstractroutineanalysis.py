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

import copy
import json
import logging
from collections import OrderedDict
from datetime import timedelta

from AccessControl import ClassSecurityInfo
from bika.lims import api
from bika.lims import bikaMessageFactory as _
from bika.lims.browser.widgets import DecimalWidget
from bika.lims.content.abstractanalysis import AbstractAnalysis
from bika.lims.content.abstractanalysis import schema
from bika.lims.content.attachment import Attachment
from bika.lims.content.clientawaremixin import ClientAwareMixin
from bika.lims.interfaces import IAnalysis
from bika.lims.interfaces import ICancellable
from bika.lims.interfaces import IDynamicResultsRange
from bika.lims.interfaces import IInternalUse
from bika.lims.interfaces import IRoutineAnalysis
from bika.lims.interfaces.analysis import IRequestAnalysis
from bika.lims.workflow import getTransitionDate
from Products.Archetypes.Field import BooleanField
from Products.Archetypes.Field import StringField
from Products.Archetypes.Schema import Schema
from Products.ATContentTypes.utils import DT2dt
from Products.ATContentTypes.utils import dt2DT
from Products.CMFCore.permissions import View
from senaite.core.catalog.indexer.baseanalysis import sortable_title
from senaite.core.permissions import FieldEditAnalysisResult
from zope.interface import alsoProvides
from zope.interface import implements
from zope.interface import noLongerProvides

logger = logging.getLogger("senaite.analysis")
# The actual uncertainty for this analysis' result, populated when the result
# is submitted.
Uncertainty = StringField(
    "Uncertainty",
    read_permission=View,
    write_permission=FieldEditAnalysisResult,
    precision=10,
    widget=DecimalWidget(
        label=_("Uncertainty")
    )
)
# This field keep track if the field hidden has been set manually or not. If
# this value is false, the system will assume the visibility of this analysis
# in results report will depend on the value set at AR, Profile or Template
# levels (see AnalysisServiceSettings fields in AR). If the value for this
# field is set to true, the system will assume the visibility of the analysis
# will only depend on the value set for the field Hidden (bool).
HiddenManually = BooleanField(
    'HiddenManually',
    default=False,
)

schema = schema.copy() + Schema((
    Uncertainty,
    HiddenManually,
))

SIBLING_SERVICE_PRIORITY = [
    "hla-i-ii",
    "hla-bioinformatics",
]


class AbstractRoutineAnalysis(AbstractAnalysis, ClientAwareMixin):
    implements(IAnalysis, IRequestAnalysis, IRoutineAnalysis, ICancellable)
    security = ClassSecurityInfo()
    displayContentsTab = False
    schema = schema

    @security.public
    def getRequest(self):
        """Returns the Analysis Request this analysis belongs to.
        Delegates to self.aq_parent
        """
        ar = self.aq_parent
        return ar

    @security.public
    def getRequestID(self):
        """Used to populate catalog values.
        Returns the ID of the parent analysis request.
        """
        ar = self.getRequest()
        if ar:
            return ar.getId()

    @security.public
    def getRequestUID(self):
        """Returns the UID of the parent analysis request.
        """
        ar = self.getRequest()
        if ar:
            return ar.UID()

    @security.public
    def getRequestURL(self):
        """Returns the url path of the Analysis Request object this analysis
        belongs to. Returns None if there is no Request assigned.
        :return: the Analysis Request URL path this analysis belongs to
        :rtype: str
        """
        request = self.getRequest()
        if request:
            return request.absolute_url_path()

    def getClient(self):
        """Returns the Client this analysis is bound to, if any
        """
        request = self.getRequest()
        return request and request.getClient() or None

    @security.public
    def getClientOrderNumber(self):
        """Used to populate catalog values.
        Returns the ClientOrderNumber of the associated AR
        """
        request = self.getRequest()
        if request:
            return request.getClientOrderNumber()

    @security.public
    def getDateReceived(self):
        """Used to populate catalog values.
        Returns the date the Analysis Request this analysis belongs to was
        received. If the analysis was created after, then returns the date
        the analysis was created.
        """
        request = self.getRequest()
        if request:
            ar_date = request.getDateReceived()
            if ar_date and self.created() > ar_date:
                return self.created()
            return ar_date
        return None

    @security.public
    def isSampleReceived(self):
        """Returns whether if the Analysis Request this analysis comes from has
        been received or not
        """
        sample = self.getRequest()
        if sample.getDateReceived():
            return True
        return False

    @security.public
    def getDatePublished(self):
        """Used to populate catalog values.
        Returns the date on which the "publish" transition was invoked on this
        analysis.
        """
        return getTransitionDate(self, 'publish', return_as_datetime=True)

    @security.public
    def getDateSampled(self):
        """Returns the date when the Sample was Sampled
        """
        request = self.getRequest()
        if request:
            return request.getDateSampled()
        return None

    @security.public
    def isSampleSampled(self):
        """Returns whether if the Analysis Request this analysis comes from has
        been received or not
        """
        return self.getDateSampled() and True or False

    @security.public
    def getStartProcessDate(self):
        """Returns the date time when the analysis request the analysis belongs
        to was received. If the analysis request hasn't yet been received,
        returns None
        Overrides getStartProcessDateTime from the base class
        :return: Date time when the analysis is ready to be processed.
        :rtype: DateTime
        """
        return self.getDateReceived()

    @security.public
    def getSamplePoint(self):
        request = self.getRequest()
        if request:
            return request.getSamplePoint()
        return None

    @security.public
    def getDueDate(self):
        """Used to populate getDueDate index and metadata.
        This calculates the difference between the time the analysis processing
        started and the maximum turnaround time. If the analysis has no
        turnaround time set or is not yet ready for proces, returns None
        """
        tat = self.getMaxTimeAllowed()
        if not tat:
            return None
        if api.to_minutes(**tat) == 0:
            return None
        start = self.getStartProcessDate()
        if not start:
            return None

        # delta time when the first analysis is considered as late
        delta = timedelta(minutes=api.to_minutes(**tat))

        # calculated due date
        end = dt2DT(DT2dt(start) + delta)

        # delta is within one day, return immediately
        if delta.days == 0:
            return end

        # get the laboratory workdays
        setup = api.get_setup()
        workdays = setup.getWorkdays()

        # every day is a workday, no need for calculation
        if workdays == tuple(map(str, range(7))):
            return end

        # reset the due date to the received date, and add only for configured
        # workdays another day
        due_date = end - delta.days

        days = 0
        while days < delta.days:
            # add one day to the new due date
            due_date += 1
            # skip if the weekday is a non working day
            if str(due_date.asdatetime().weekday()) not in workdays:
                continue
            days += 1

        return due_date

    @security.public
    def getSampleType(self):
        request = self.getRequest()
        if request:
            return request.getSampleType()
        return None

    @security.public
    def getSampleTypeUID(self):
        """Used to populate catalog values.
        """
        sample = self.getRequest()
        if not sample:
            return None
        return sample.getRawSampleType()

    @security.public
    def getResultsRange(self):
        """Returns the valid result range for this routine analysis

        A routine analysis will be considered out of range if it result falls
        out of the range defined in "min" and "max". If there are values set
        for "warn_min" and "warn_max", these are used to compute the shoulders
        in both ends of the range. Thus, an analysis can be out of range, but
        be within shoulders still.

        :return: A dictionary with keys "min", "max", "warn_min" and "warn_max"
        :rtype: dict
        """
        return self.getField("ResultsRange").get(self)

    @security.private
    def setResultsRange(self, spec, update_dynamic_spec=True):
        """Set the results range for this routine analysis

        NOTE: This custom setter also applies dynamic specifications

        :param spec: The result range to set
        :param update_dynamic_spec: If True, dynamic specifications are update
        """
        adapter = IDynamicResultsRange(self, None)
        if adapter and update_dynamic_spec:
            # update the result range with the dynamic values
            spec.update(adapter())
        field = self.getField("ResultsRange")
        field.set(self, spec)

    @security.public
    def getSiblings(self, with_retests=False):
        """
        Return the siblings analyses, using the parent to which the current
        analysis belongs to as the source
        :param with_retests: If false, siblings with retests are dismissed
        :type with_retests: bool
        :return: list of siblings for this analysis
        :rtype: list of IAnalysis
        """
        raise NotImplementedError("getSiblings is not implemented.")

    @security.public
    def getCalculation(self):
        """Return current assigned calculation
        """
        field = self.getField("Calculation")
        calculation = field.get(self)
        if not calculation:
            return None
        return calculation

    @security.public
    def setCalculation(self, value):
        self.getField("Calculation").set(self, value)
        # TODO Something weird here
        # Reset interims so they get extended with those from calculation
        # see bika.lims.browser.fields.interimfieldsfield.set
        interim_fields = copy.deepcopy(self.getInterimFields())
        self.setInterimFields(interim_fields)

    @security.public
    def getDependents(self, with_retests=False, recursive=False):
        """
        Returns a list of siblings who depend on us to calculate their result.
        :param with_retests: If false, dependents with retests are dismissed
        :param recursive: If true, returns all dependents recursively down
        :type with_retests: bool
        :return: Analyses the current analysis depends on
        :rtype: list of IAnalysis
        """

        def is_dependent(analysis):
            # Never consider myself as dependent
            if analysis.UID() == self.UID():
                return False

            # Never consider analyses from same service as dependents
            self_service_uid = self.getRawAnalysisService()
            if analysis.getRawAnalysisService() == self_service_uid:
                return False

            # Without calculation, no dependency relationship is possible
            calculation = analysis.getCalculation()
            if not calculation:
                return False

            # Calculation must have the service I belong to
            services = calculation.getRawDependentServices()
            return self_service_uid in services

        request = self.getRequest()
        if request.isPartition():
            parent = request.getParentAnalysisRequest()
            siblings = parent.getAnalyses(full_objects=True)
        else:
            siblings = self.getSiblings(with_retests=with_retests)

        dependents = filter(lambda sib: is_dependent(sib), siblings)
        if not recursive:
            return dependents

        # Return all dependents recursively
        deps = dependents
        for dep in dependents:
            down_dependencies = dep.getDependents(with_retests=with_retests,
                                                  recursive=True)
            deps.extend(down_dependencies)
        return deps

    @security.public
    def getDependencies(self, with_retests=False, recursive=False):
        """
        Return a list of siblings who we depend on to calculate our result.
        :param with_retests: If false, siblings with retests are dismissed
        :param recursive: If true, looks for dependencies recursively up
        :type with_retests: bool
        :return: Analyses the current analysis depends on
        :rtype: list of IAnalysis
        """
        calc = self.getCalculation()
        if not calc:
            return []

        # If the calculation this analysis is bound does not have analysis
        # keywords (only interims), no need to go further
        service_uids = calc.getRawDependentServices()

        # Ensure we exclude ourselves
        service_uid = self.getRawAnalysisService()
        service_uids = filter(lambda serv: serv != service_uid, service_uids)
        if len(service_uids) == 0:
            return []

        dependencies = []
        for sibling in self.getSiblings(with_retests=with_retests):
            # We get all analyses that depend on me, also if retracted (maybe
            # I am one of those that are retracted!)
            deps = map(api.get_uid, sibling.getDependents(with_retests=True))
            if self.UID() in deps:
                dependencies.append(sibling)
                if recursive:
                    # Append the dependencies of this dependency
                    up_deps = sibling.getDependencies(with_retests=with_retests,
                                                      recursive=True)
                    dependencies.extend(up_deps)

        # Exclude analyses of same service as me to prevent max recursion depth
        return filter(lambda dep: dep.getRawAnalysisService() != service_uid,
                      dependencies)

    @security.public
    def getPrioritySortkey(self):
        """
        Returns the key that will be used to sort the current Analysis, from
        most prioritary to less prioritary.
        :return: string used for sorting
        """
        analysis_request = self.getRequest()
        if analysis_request is None:
            return None
        ar_sort_key = analysis_request.getPrioritySortkey()
        ar_id = analysis_request.getId().lower()
        title = sortable_title(self)
        if callable(title):
            title = title()
        return '{}.{}.{}'.format(ar_sort_key, ar_id, title)

    @security.public
    def getHidden(self):
        """ Returns whether if the analysis must be displayed in results
        reports or not, as well as in analyses view when the user logged in
        is a Client Contact.

        If the value for the field HiddenManually is set to False, this function
        will delegate the action to the method getAnalysisServiceSettings() from
        the Analysis Request.

        If the value for the field HiddenManually is set to True, this function
        will return the value of the field Hidden.
        :return: true or false
        :rtype: bool
        """
        if self.getHiddenManually():
            return self.getField('Hidden').get(self)
        request = self.getRequest()
        if request:
            service_uid = self.getServiceUID()
            ar_settings = request.getAnalysisServiceSettings(service_uid)
            return ar_settings.get('hidden', False)
        return False

    @security.public
    def setHidden(self, hidden):
        """ Sets if this analysis must be displayed or not in results report and
        in manage analyses view if the user is a lab contact as well.

        The value set by using this field will have priority over the visibility
        criteria set at Analysis Request, Template or Profile levels (see
        field AnalysisServiceSettings from Analysis Request. To achieve this
        behavior, this setter also sets the value to HiddenManually to true.
        :param hidden: true if the analysis must be hidden in report
        :type hidden: bool
        """
        self.setHiddenManually(True)
        self.getField('Hidden').set(self, hidden)

    @security.public
    def setInternalUse(self, internal_use):
        """Applies the internal use of this Analysis. Analyses set for internal
        use are not accessible to clients and are not visible in reports
        """
        if internal_use:
            alsoProvides(self, IInternalUse)
        else:
            noLongerProvides(self, IInternalUse)

    def getConditions(self, empties=False, recursive=True):
        sample = self.getRequest()
        service_uid = self.getRawAnalysisService()
        my_uid = self.UID()

        sample_conditions = sample.getServiceConditions() or []

        # 优先取精确匹配自己的条件（带 analysis_uid 的新格式）
        exact = [c for c in sample_conditions
                 if c.get("uid") == service_uid and c.get("analysis_uid") == my_uid]
        if exact:
            existing = exact
        else:
            # 兼容旧数据：没有 analysis_uid 时按 service_uid 匹配
            existing = filter(lambda c: c.get("uid") == service_uid and not c.get("analysis_uid"),
                              sample_conditions)

        defs = self._get_service_condition_definitions()
        conditions, changed = self._merge_conditions_with_definitions(existing, defs, service_uid)

        for cond in conditions:
            value = cond.get("value", "")
            if isinstance(value, str):
                try:
                    value = value.decode("utf-8")
                except Exception:
                    pass
                cond["value"] = value

            title = cond.get("title", "")
            if not title or value:
                continue

            reused_value = None
            if title in self._get_sample_field_map():
                reused_value = self._get_value_from_sample_field(title)

            else:
                reused_value = self._get_value_from_siblings(title)
            if reused_value:
                cond["value"] = reused_value

        if changed:
            try:
                self.setConditions(
                    conditions)  # setConditions 最终会 sample.setServiceConditions(...) :contentReference[oaicite:2]{index=2}
            except Exception as e:
                logger.warning("[CONDITIONS] backfill failed: %s", e)

        return copy.deepcopy(conditions)

    def setConditions(self, conditions):
        if not conditions:
            conditions = []
        sample = self.getRequest()
        service_uid = self.getRawAnalysisService()
        sample_conditions = sample.getServiceConditions()
        sample_conditions = copy.deepcopy(sample_conditions)

        # 精确排除属于自己的旧条件（新格式带 analysis_uid，旧格式不带）
        my_uid = self.UID()
        other_conditions = [c for c in sample_conditions
                            if not (c.get("uid") == service_uid and
                                    c.get("analysis_uid") == my_uid)]

        def to_condition(condition):
            condition = dict(condition)

            title = condition.get("title")
            cond_type = condition.get("type")
            if not all([title, cond_type]):
                return None

            # 自动复用值（如果为空）
            value = condition.get("value", "")
            # if not value or str(value).strip() == "":
            if not value or (u"%s" % value).strip() == u"":
                reused_value = self._get_value_from_siblings(title)
                if reused_value:
                    condition["value"] = reused_value

            # 构造最终结构，写入 analysis_uid 确保每个实验条件独立
            condition_info = {
                "uid": service_uid,
                "analysis_uid": my_uid,
                "type": cond_type,
                "title": title,
                "description": "",
                "choices": "",
                "default": "",
                "required": "",
                "value": "",
            }

            condition_info.update(condition)
            # 确保 analysis_uid 不被 update 覆盖
            condition_info["analysis_uid"] = my_uid
            return condition_info

        conditions = filter(None, [to_condition(cond) for cond in conditions])

        # 附件处理略（不动原逻辑）
        attachments = []
        for condition in conditions:
            if condition.get("type") != "file":
                continue
            value = condition.get("value")
            orig_attachment = condition.pop("attachment", None)
            if api.is_uid(value):
                # link to an existing attachment
                attachments.append(value)
            elif isinstance(value, Attachment):
                # link to an existing attachment
                uid = api.get_uid(value)
                attachments.append(uid)
                condition["value"] = uid
            elif getattr(value, "filename", ""):
                # create new attachment
                client = sample.getClient()
                att = api.create(client, "Attachment", AttachmentFile=value)
                attachments.append(api.get_uid(att))
                # update with the attachment uid
                condition["value"] = api.get_uid(att)
            elif api.is_uid(orig_attachment):
                # restore to original attachment
                attachments.append(orig_attachment)
                condition["value"] = orig_attachment
            else:
                # nothing we can handle, update with an empty value
                condition["value"] = ""

        if attachments:
            attachments.extend(self.getRawAttachment() or [])
            attachments = list(OrderedDict.fromkeys(attachments))
            self.setAttachment(attachments)

        sample.setServiceConditions(other_conditions + conditions)

    @security.public
    def getPrice(self):
        """The function obtains the analysis' price without VAT and without
        member discount
        :return: the price (without VAT or Member Discount) in decimal format
        """
        client = self.getClient()
        if client and client.getBulkDiscount():
            return self.getBulkPrice()
        return self.getField('Price').get(self)

    def _get_service_keyword(self, analysis):
        try:
            # svc = analysis.getService()
            svc = analysis.getAnalysisService()
            if svc:
                try:
                    kw = svc.getKeyword()
                except Exception:
                    kw = getattr(svc, "keyword", "")
                return kw or ""
        except Exception:
            pass

        try:
            svc = analysis.getAnalysisService()
            if svc:
                try:
                    kw = svc.getKeyword()
                except Exception:
                    kw = getattr(svc, "keyword", "")
                return kw or ""
        except Exception:
            pass

        return ""

    def _get_priority_rank(self, analysis):
        kw = self._get_service_keyword(analysis)
        try:
            return SIBLING_SERVICE_PRIORITY.index(kw)
        except ValueError:
            return 999

    def _rank_sibling(self, analysis):
        """排序 key：先按优先级，再按 created"""
        rank = self._get_priority_rank(analysis)
        try:
            created = analysis.created()
        except Exception:
            created = 0
        return (rank, created)

    def _get_conditions_raw(self, analysis):
        field = None
        try:
            field = analysis.Schema().get("Conditions")
        except Exception:
            field = None

        if not field:
            try:
                field = analysis.getField("Conditions")
            except Exception:
                field = None

        if not field:
            return []
        try:
            raw = field.getRaw(analysis)
        except Exception:
            try:
                raw = field.get(analysis)
            except Exception:
                raw = None
        if not raw:
            return []
        if isinstance(raw, basestring):
            s = raw.strip()
            if (s.startswith("[") and s.endswith("]")) or (s.startswith("{") and s.endswith("}")):
                try:
                    raw = json.loads(s)
                except Exception:
                    return []
            else:
                return []

        if isinstance(raw, dict):
            raw = [raw]
        if not isinstance(raw, (list, tuple)):
            return []

        return list(raw)

    def _get_value_from_siblings(self, title):
        sample = self.getRequest()
        if not sample:
            logger.warning("[SIBLING] 无法获取 Sample 对象")
            return None

        req = getattr(self, "REQUEST", None)
        guard_key = "_sibling_lookup_guard"
        token = "%s|%s" % (getattr(self, "getId", lambda: repr(self))(), title)

        if req is not None:
            guard = req.get(guard_key)
            if guard is None:
                guard = set()
                req[guard_key] = guard
            if token in guard:
                logger.warning("[SIBLING] 递归保护触发: %s", token)
                return None
            guard.add(token)

        try:
            service_uid = self.getRawAnalysisService()

            analyses = list(sample.getAnalyses(full_objects=True) or [])

            current_rank = self._get_priority_rank(self)

            siblings = []
            for analysis in analyses:
                if analysis == self:
                    continue
                try:
                    if analysis.getRawAnalysisService() == service_uid:
                        continue
                except Exception:
                    pass

                arank = self._get_priority_rank(analysis)
                if current_rank != 999 and arank >= current_rank:
                    continue
                siblings.append(analysis)

            siblings = sorted(siblings, key=self._rank_sibling)
            for analysis in siblings:
                conditions = self._get_conditions_raw(analysis)
                for cond in conditions:
                    if cond.get("title") == title and cond.get("value"):
                        # logger.info("[SIBLING] 从 %s 复用字段 '%s' 值：%s",
                        #             analysis.getId(), title, cond.get("value"))
                        return cond.get("value")

                interims = analysis.getInterimFields() or []
                for field in interims:
                    if field.get("title") == title:
                        value = field.get("value")
                        if value not in ("", None):
                            # logger.info("[SIBLING] interim 字段复用字段 '%s' 值：%s",
                            #             title, value)
                            return value

            return None

        finally:
            if req is not None:
                try:
                    req.get(guard_key, set()).discard(token)
                except Exception:
                    pass

    def _get_sample_field_map(self):
        return {
            "样本类型": "getSampleTypeTitle",
            "癌种": "getCancerType",
            "国籍": "getEthnicity",
            "受试者唯一编码": "getSubjectUID",
            "疾病诊断": "getDiagnosis",
        }

    def _get_value_from_sample_field(self, title):
        sample = self.getRequest()

        if not sample:
            logger.warning("[DEBUG] 当前 Analysis 没有关联 Sample")
            return None
        title_map = self._get_sample_field_map()
        field_method = title_map.get(title)
        if not field_method or not hasattr(sample, field_method):
            logger.warning("[DEBUG] 样本字段不存在方法: %s", field_method)
            return None

        # logger.info("[DEBUG] 调用样本方法：%s", field_method)
        value = getattr(sample, field_method)()
        # logger.info("[DEBUG] 返回值类型: %s，值: %s", type(value), value)

        if hasattr(value, 'Title'):
            return value.Title()
        return value

    def _get_service_condition_definitions(self):
        """从 AnalysisService 读取最新 Condition 定义（模板）"""
        service = None
        try:
            service = self.getAnalysisService()
        except Exception:
            service = None

        if not service:
            uid = self.getRawAnalysisService()
            service = api.get_object_by_uid(uid)

        if not service:
            return []

        # 常见：服务对象上有 getConditions()
        if hasattr(service, "getConditions"):
            return service.getConditions() or []

        # 兼容：从 AT 字段取
        field = service.getField("Conditions") if hasattr(service, "getField") else None
        return (field and field.get(service)) or []

    def _merge_conditions_with_definitions(self, existing, definitions, service_uid):
        """把 sample 里已有的 conditions 与服务模板合并，缺失的补齐"""
        existing_by_title = {}
        for c in existing:
            t = (c or {}).get("title")
            if t:
                existing_by_title[t] = c

        merged = []
        used = set()
        changed = False

        for d in definitions or []:
            title = (d or {}).get("title")
            ctype = (d or {}).get("type")
            if not title or not ctype:
                continue

            if title in existing_by_title:
                merged.append(existing_by_title[title])
            else:
                # 缺失项：按模板造一条空行（value 先 default/空）
                newc = dict(d)
                newc["uid"] = service_uid
                newc.setdefault("value", newc.get("default", "") or "")
                merged.append(newc)
                changed = True

            used.add(title)

        # 把历史遗留的（模板里没有，但 sample 里有）也保留，避免丢数据
        for c in existing:
            t = (c or {}).get("title")
            if t and t not in used:
                merged.append(c)

        return merged, changed

#
# old.setConditions([{'uid': service_uid,'type': 'multiselect','title': u'染色靶标','value': ['MAGE-A4'],'description': u'请选择本次检测的目标基因','choices': 'MAGE-A4|ROPN1|MART-1|AFP|gp100|HLA-I|CD8|NY-ESO-1|PRAME','default': '','required': '',}])
#
# retest.setConditions([{'uid': service_uid,'type': 'multiselect','title': u'染色靶标','value': ['gp100', 'NY-ESO-1', 'PRAME'],'description': u'请选择本次检测的目标基因','choices': 'MAGE-A4|ROPN1|MART-1|AFP|gp100|HLA-I|CD8|NY-ESO-1|PRAME','default': '','required': '',}])
