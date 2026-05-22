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

import copy
import logging

from bika.lims import api
from bika.lims import FieldEditAnalysisConditions
from bika.lims import senaiteMessageFactory as _
from bika.lims.api.security import check_permission
from bika.lims.content.attachment import Attachment
from bika.lims.interfaces.analysis import IRequestAnalysis
from Products.CMFPlone.utils import safe_unicode
from Products.Five.browser import BrowserView
from Products.Five.browser.pagetemplatefile import ViewPageTemplateFile

logger = logging.getLogger("senaite.analysis")

# --- Py2/Py3 text compat ---
try:
    string_types = (basestring,)  # noqa
    text_type = unicode  # noqa
except NameError:  # py3
    string_types = (str,)
    text_type = str


def _as_text(x):
    if x is None:
        return u""
    try:
        return text_type(x)
    except Exception:
        try:
            return text_type(x, "utf-8", "ignore")
        except Exception:
            return text_type(x)


def _is_empty_value(v):
    """True if v is considered empty for conditions"""
    if v is None:
        return True
    if isinstance(v, (list, tuple)):
        return len(v) == 0
    if isinstance(v, string_types):
        return _as_text(v).strip() == u""
    # other types
    return _as_text(v).strip() == u""

def _normalize_multiselect_value(raw):
    """Normalize multiselect value to list[str], where each item is a full option
    e.g. 'HLA-A,HLA-B,HLA-C' is ONE item (comma inside option must be preserved)

    Supports:
      None -> []
      'opt1|opt2' -> ['opt1','opt2']
      'opt1' -> ['opt1']
      ['opt1','opt2'] -> ['opt1','opt2']
    """
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        vals = []
        for v in raw:
            t = _as_text(v).strip()
            if t:
                vals.append(t)
        return vals
    if isinstance(raw, string_types):
        s = _as_text(raw).strip()
        if not s:
            return []
        if u"|" in s:
            return [p.strip() for p in s.split(u"|") if p.strip()]
        return [s]
    s = _as_text(raw).strip()
    return [s] if s else []


class SetAnalysisConditionsView(BrowserView):
    """View for the update of analysis conditions"""
    template = ViewPageTemplateFile("templates/set_analysis_conditions.pt")

    def __call__(self):
        if self.request.form.get("submitted", False):
            return self.handle_submit()
        return self.template()

    def redirect(self, message=None, level="info"):
        redirect_url = api.get_url(self.context)
        if message is not None:
            self.context.plone_utils.addPortalMessage(message, level)
        return self.request.response.redirect(redirect_url)

    def get_uid_from_request(self):
        uid = self.request.form.get("uid", self.request.get("uid"))
        if api.is_uid(uid):
            return uid
        return None

    def get_analysis(self):
        uid = self.get_uid_from_request()
        obj = api.get_object_by_uid(uid)
        if IRequestAnalysis.providedBy(obj):
            return obj
        return None

    def get_analysis_name(self):
        analysis = self.get_analysis()
        return api.get_title(analysis) if analysis else u""

    # -------------------------------------------------------------------------
    # Defaults source: AnalysisService conditions (Advanced tab)
    # -------------------------------------------------------------------------
    def _get_service_for_analysis(self, analysis):
        """Try to fetch the AnalysisService object from an Analysis"""
        if not analysis:
            return None

        # different versions may expose different accessors
        for name in ("getService", "getAnalysisService", "getServiceUID", "getServiceUid"):
            acc = getattr(analysis, name, None)
            if not callable(acc):
                continue

            try:
                svc = acc()
            except Exception:
                svc = None

            # UID accessor variants
            if api.is_uid(svc):
                try:
                    return api.get_object_by_uid(svc, None)
                except Exception:
                    return None

            # object already
            if svc:
                return svc

        # fallback: analysis has Service UID in schema field?
        try:
            uid = getattr(analysis, "getServiceUID", None)
            if callable(uid):
                uid = uid()
                if api.is_uid(uid):
                    return api.get_object_by_uid(uid, None)
        except Exception:
            pass

        return None

    def _get_service_conditions_by_title(self, analysis):
        """Return {title: service_condition_dict} for current service"""
        svc = self._get_service_for_analysis(analysis)
        if not svc:
            return {}

        svc_conds = []
        for name in ("getConditions",):
            acc = getattr(svc, name, None)
            if callable(acc):
                try:
                    # use empties=True if supported
                    try:
                        svc_conds = acc(empties=True)
                    except TypeError:
                        svc_conds = acc()
                except Exception:
                    svc_conds = []
            if svc_conds is not None:
                break

        mapping = {}
        for c in svc_conds or []:
            title = (_as_text(c.get("title")) or u"").strip()
            if title:
                mapping[title] = c
        return mapping

    def _get_default_value_from_service_condition(self, svc_cond):
        if not svc_cond:
            return u""
        dv = (svc_cond.get("default_value") or
              svc_cond.get("default") or
              svc_cond.get("defaultValue") or
              svc_cond.get("defaultvalue"))
        return dv

    # -------------------------------------------------------------------------
    # Sibling reuse (your existing rule)
    # -------------------------------------------------------------------------
    def _get_value_from_siblings(self, title):
        analysis = self.get_analysis()
        if not analysis:
            return None

        sample = getattr(analysis, "aq_parent", None)
        if not sample or not hasattr(sample, "getAnalyses"):
            return None

        try:
            current_uid = api.get_uid(analysis)
        except Exception:
            current_uid = None

        siblings = []
        try:
            for b in sample.getAnalyses():
                try:
                    if api.get_uid(b) != current_uid:
                        siblings.append(b)
                except Exception:
                    siblings.append(b)
        except Exception:
            return None

        for sibling_brain in siblings:
            try:
                sibling = api.get_object(sibling_brain)
            except Exception:
                sibling = sibling_brain

            if not sibling or not hasattr(sibling, "getConditions"):
                continue

            try:
                conds = sibling.getConditions()
            except Exception:
                conds = []

            for cond in conds or []:
                if cond.get("title") == title and not _is_empty_value(cond.get("value")):
                    return cond.get("value")
        return None

    def get_conditions(self):
        analysis = self.get_analysis()
        if not analysis:
            return []

        try:
            conditions = analysis.getConditions(empties=True)
            conditions = copy.deepcopy(conditions)
        except Exception as e:
            logger.error("[get_conditions] failed reading analysis conditions: %s", e, exc_info=True)
            return []

        # identify first analysis (to decide sibling reuse)
        is_first_analysis = True
        try:
            sample = analysis.aq_parent
            analyses = sample.getAnalyses() if sample else []
            current_uid = api.get_uid(analysis)
            first_uid = api.get_uid(analyses[0]) if analyses else None
            is_first_analysis = (first_uid == current_uid)
        except Exception as e:
            logger.warning("[get_conditions] failed checking first analysis: %s", e)
            is_first_analysis = True

        # service conditions map for defaults
        svc_map = {}
        try:
            svc_map = self._get_service_conditions_by_title(analysis)
        except Exception:
            svc_map = {}

        for condition in conditions:
            try:
                logger.warning("[get_conditions] raw condition: %r", condition)
                condition.setdefault("choices", u"")
                condition.setdefault("required", u"off")
                condition.setdefault("report", u"off")
                condition.setdefault("description", u"")

                ctype = (_as_text(condition.get("type")) or u"").strip()
                title = (_as_text(condition.get("title")) or u"").strip()
                desc = (_as_text(condition.get("description")) or u"").strip()
                condition["type"] = ctype
                condition["title"] = title
                condition["description"] = desc

                # parse options from choices
                choices = condition.get("choices", "")
                if isinstance(choices, string_types):
                    options = [seg.strip() for seg in _as_text(choices).split(u"|") if seg.strip()]
                else:
                    options = []
                condition["options"] = options

                # 1) sibling reuse (only if not first analysis and current value empty)
                if not is_first_analysis and _is_empty_value(condition.get("value")) and title:
                    reused = self._get_value_from_siblings(title)
                    if not _is_empty_value(reused):
                        condition["value"] = reused

                # 2) default from AnalysisService (if still empty)
                if _is_empty_value(condition.get("value")) and title and title in svc_map:
                    dv = self._get_default_value_from_service_condition(svc_map.get(title))
                    if not _is_empty_value(dv):
                        condition["value"] = dv

                # file condition: attach info
                if ctype == "file":
                    uid = condition.get("value")
                    condition["attachment"] = self.get_attachment_info(uid)

                # multiselect condition: normalize to list[str]
                if ctype == "multiselect":
                    condition["value"] = _normalize_multiselect_value(condition.get("value"))

            except Exception as e:
                logger.warning("[get_conditions] failed processing condition %r: %s", condition, e, exc_info=True)

        # keep "file" types last (py2 cmp)
        def files_last(c1, c2):
            t1 = c1.get("type")
            t2 = c2.get("type")
            if "file" not in [t1, t2]:
                return 0
            return 1 if t1 == "file" else -1

        try:
            return sorted(conditions, cmp=files_last)
        except Exception:
            return conditions

    def get_attachment_info(self, uid):
        attachment = api.get_object_by_uid(uid, default=None)
        if not isinstance(attachment, Attachment):
            return {}

        url = api.get_url(attachment)
        at_file = attachment.getAttachmentFile()
        return {
            "uid": api.get_uid(attachment),
            "id": api.get_id(attachment),
            "url": url,
            "download_url": "{}/at_download/AttachmentFile".format(url),
            "filename": getattr(at_file, "filename", ""),
        }

    # -------------------------------------------------------------------------
    # Submit
    # -------------------------------------------------------------------------
    def handle_submit(self):
        analysis = self.get_analysis()
        title = safe_unicode(api.get_title(analysis)) if analysis else u""

        if not analysis:
            return self.redirect(message=_("No analysis found"), level="error")

        if not check_permission(FieldEditAnalysisConditions, analysis):
            message = _("Not allowed to update conditions: {}").format(title)
            return self.redirect(message=message, level="error")

        # request.form supplies records list of mappings
        conditions = self.request.form.get("conditions", [])
        conditions = [dict(c) for c in conditions]

        # multiselect: store as "opt1|opt2|opt3" (NOT comma, because option itself contains commas)
        for cond in conditions:
            if (cond.get("type") or "").strip() == "multiselect":
                val = cond.get("value")
                if isinstance(val, (list, tuple)):
                    cleaned = []
                    for v in val:
                        t = _as_text(v).strip()
                        if t:
                            cleaned.append(t)
                    cond["value"] = u"|".join(cleaned)
                elif isinstance(val, string_types):
                    cond["value"] = _as_text(val).strip()
                else:
                    cond["value"] = u""

        # keep original order as initially set on analysis
        original = analysis.getConditions(empties=True)
        original_titles = [c.get("title") for c in (original or [])]

        def original_order(c1, c2):
            t1 = c1.get("title")
            t2 = c2.get("title")
            try:
                i1 = original_titles.index(t1)
            except Exception:
                i1 = 10 ** 9
            try:
                i2 = original_titles.index(t2)
            except Exception:
                i2 = 10 ** 9
            return (i1 > i2) - (i1 < i2)

        try:
            conditions = sorted(conditions, cmp=original_order)
        except Exception:
            pass

        analysis.setConditions(conditions)
        message = _("Analysis conditions updated: {}").format(title)
        return self.redirect(message=message)

    # optional helper if your PT uses it
    def is_readonly_field(self, condition):
        """Kept for compatibility with your earlier code; not required for defaults."""
        try:
            value = _as_text(condition.get("value", u"")).strip()
        except Exception:
            value = u""
        if not value:
            return False

        mtool = api.get_tool("portal_membership")
        member = mtool.getAuthenticatedMember()
        roles = member.getRolesInContext(self.context)
        return not any(role in roles for role in ("LabManager", "Manager"))
