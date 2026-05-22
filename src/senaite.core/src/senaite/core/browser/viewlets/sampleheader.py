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

from itertools import islice

from bika.lims import api
from bika.lims import senaiteMessageFactory as _
from bika.lims.api.security import check_permission
from bika.lims.api.security import get_roles
from bika.lims.interfaces import IAnalysisRequestWithPartitions
from bika.lims.interfaces import IHeaderTableFieldRenderer
from bika.lims.interfaces.field import IUIDReferenceField
from plone.app.layout.viewlets import ViewletBase
from plone.memoize import view as viewcache
from plone.protect import PostOnly
from Products.Archetypes.event import ObjectEditedEvent
from Products.Archetypes.interfaces import IField as IATField
from Products.CMFCore.permissions import ModifyPortalContent
from Products.Five.browser.pagetemplatefile import ViewPageTemplateFile
from senaite.core import logger
from senaite.core.interfaces import IDataManager
from zope import event
from zope.component import queryAdapter
from zope.schema.interfaces import IField as IDXField

_fieldname_not_in_form = object()

HOSPITAL_CLIENT_UIDS = ["d132afb7101443bdb242507de7c9b2f1"]


class SampleHeaderViewlet(ViewletBase):
    """Header table with editable sample fields
    """
    template = ViewPageTemplateFile("templates/sampleheader.pt")

    RECEIVING_CHECK_FIELDS = (
        "over24h",
        "tempValid",
        "labelMatch",
        "pkgStatus",
        "EnvironmentalConditions",
        "pkgIntact",
        "pkgStatusNote",
        "TrueDateReceived",
    )

    def can_view_receiving(self):
        roles = set(get_roles())
        allowed = {"Manager", "LabClerk"}
        return bool(roles & allowed)

    def render(self):
        """Renders the viewlet and handles form submission
        """
        request = self.request
        submitted = request.form.get("sampleheader_form_submitted", False)
        save = request.form.get("sampleheader_form_save", False)
        errors = {}
        if submitted and save:
            errors = self.handle_form_submit(request=self.request)
            # NOTE: we only redirect if no validation errors occured,
            #       because otherwise the fields are not error-indicated!
            if not errors:
                # redirect needed to show status message
                self.request.response.redirect(self.context.absolute_url())
        return self.template(errors=errors)

    def handle_form_submit(self, request=None):
        """Handle form submission
        """
        PostOnly(request)

        errors = {}
        field_values = {}
        form = request.form

        # for name, field in self.fields.items():
        # get the raw value from the form
        section = form.get("section", "").strip().lower()
        for name, field in self._iter_fields_by_section(section).items():
            value = self.get_field_value(field, form)
            if value is _fieldname_not_in_form:
                continue

            # some legacy widgets need qinayitin values with <fieldname>_ as prefix
            prefix = "{}_".format(name)
            extra = filter(lambda key: key.startswith(prefix), form.keys())
            form_values = dict((key, form[key]) for key in extra)
            form_values[name] = value

            # process the value as the widget would usually do
            process_value = field.widget.process_form
            value, msgs = process_value(self.context, field, form_values)

            # Keep track of field-values
            field_values.update({name: value})

            # Validate the field values
            error = field.validate(value, self.context)
            if error:
                errors.update({name: error})

        if errors:
            return errors
        else:
            # we want to set the fields with the data manager
            dm = IDataManager(self.context)

            # Store the field values
            for name, value in field_values.items():
                dm.set(name, value)

            message = _("Changes saved.")
            # reindex the object after save to update all catalog metadata
            self.context.reindexObject()
            # notify object edited event
            event.notify(ObjectEditedEvent(self.context))
            self.add_status_message(message, level="info")

    def _receiving_fieldnames(self):
        cfg = self.get_configuration()
        standard = list(cfg.get("standard_fields", []))
        wl = set(self.RECEIVING_CHECK_FIELDS)
        names = [n for n in standard if n in self.fields and n in wl]

        if "TempControlledTransport" in names:
            names.remove("TempControlledTransport")
        return names

    def _sampling_fieldnames(self):
        """Return fieldnames for the sampling section.
        在这里根据样本类型和温度计状态动态地控制字段是否显示
        """
        cfg = self.get_configuration()
        standard = list(cfg.get("standard_fields", []))
        receiving = set(self._receiving_fieldnames())
        names = [n for n in standard if n in self.fields and n not in receiving]

        sample_type = u""
        try:
            accessor = getattr(self.context, "getSampleType", None)
            if callable(accessor):
                sample_type = accessor() or u""
        except Exception:
            logger.exception(
                "SampleHeaderViewlet: failed to get SampleType for %r",
                self.context,
            )
            sample_type = u""

        show_lesion_for = (
            u"新鲜组织",
            u"切片",
            u"蜡块",
            u"蜡卷",
        )

        if sample_type not in show_lesion_for:
            for fname in ("LesionType", "LesionDescription"):
                if fname in names:
                    names.remove(fname)

        if "TempControlledTransport" in names:
            names.remove("TempControlledTransport")

        has_tct_field = False
        tct_field = None
        try:
            getField = getattr(self.context, "getField", None)
            if callable(getField):
                tct_field = getField("TempControlledTransport")
                has_tct_field = tct_field is not None
            else:
                Schema = getattr(self.context, "Schema", None)
                if callable(Schema):
                    schema = Schema()
                    tct_field = schema.get("TempControlledTransport", None) if schema else None
                    has_tct_field = tct_field is not None
        except Exception:
            logger.exception("SampleHeaderViewlet: error checking TempControlledTransport field existence")
            has_tct_field = False
            tct_field = None

        # 旧样本：没有字段 → 完全不控制（全部照常显示）
        if not has_tct_field:
            pass
        else:
            # 新样本：字段存在 → 按值控制（只有 yes 才显示温控字段）
            tct_value = u""
            try:
                tct_value = tct_field.get(self.context) if tct_field else u""
            except Exception:
                logger.exception("SampleHeaderViewlet: failed reading TempControlledTransport value")
                tct_value = u""
            tct_norm = (u"%s" % tct_value).strip().lower()
            show_temp_fields = (tct_norm == u"yes")

            if "ThermometerStatus" in names:
                names.remove("ThermometerStatus")

            if not show_temp_fields:
                for fname in ("ThermometerCode", "TemperatureRecorded", "BoxPreCooled", "SampleIsolated"):
                    if fname in names:
                        names.remove(fname)

            # 院内编码只对特定中心显示
            client = self.context.getClient()
            client_uid = api.get_uid(client) if client else ""
            if client_uid not in HOSPITAL_CLIENT_UIDS:
                if "HospitalPatientID" in names:
                    names.remove("HospitalPatientID")

            bt_field = None
            try:
                getField = getattr(self.context, "getField", None)
                if callable(getField):
                    bt_field = getField("BloodTransfusion12M")
            except Exception:
                logger.exception(
                    "SampleHeaderViewlet: error checking BloodTransfusion12M field existence"
                )

            if bt_field is not None:
                bt_value = u""
                try:
                    bt_value = bt_field.get(self.context) or u""
                except Exception:
                    logger.exception(
                        "SampleHeaderViewlet: failed reading BloodTransfusion12M value"
                    )
                bt_norm = (u"%s" % bt_value).strip().lower()
                if bt_norm != u"yes":
                    if "BloodTransfusionHistory" in names:
                        names.remove("BloodTransfusionHistory")

        return names

    def _iter_fields_by_section(self, section):
        if section == "receiving":
            names = self._receiving_fieldnames()
        elif section == "sampling":
            names = self._sampling_fieldnames()
        else:
            return self.fields
        return {n: self.fields[n] for n in names if n in self.fields}

    def get_receiving_fields(self):
        return self._receiving_fieldnames()

    def get_sampling_fields(self):
        return self._sampling_fieldnames()

    def get_configuration(self):
        """Return header configuration

        This method retrieves the customized field and column configuration
        from the management view directly.

        :returns: Field and columns configuration dictionary
        """
        mv = api.get_view(name="manage-sample-fields", context=self.context)
        settings = mv.get_configuration()
        visibility = settings.get("field_visibility")

        def is_visible(name):
            return visibility.get(name, True)

        prominent_fields = filter(is_visible, settings.get("prominent_fields"))
        standard_fields = filter(is_visible, settings.get("standard_fields"))

        config = {}
        config.update(settings)
        config["prominent_fields"] = prominent_fields
        config["standard_fields"] = standard_fields

        return config

    @property
    def fields(self):
        """Returns an ordered dict of all schema fields
        """
        return api.get_fields(self.context)

    def get_field_value(self, field, form):
        """Returns the submitted value for the given field
        """
        fieldname = field.getName()
        if fieldname not in form:
            return _fieldname_not_in_form

        fieldvalue = form[fieldname]

        # Handle  reference fields
        if IUIDReferenceField.providedBy(field):
            value = fieldvalue

            # extract the assigned UIDs for multi-reference fields
            if field.multiValued:
                value = filter(None, fieldvalue.split("\r\n"))

            # allow to flush single reference fields
            if not field.multiValued and not fieldvalue:
                value = ""

            return value

        # other fields
        return fieldvalue

    def grouper(self, iterable, n=3):
        """Splits an iterable into chunks of `n` items
        """
        for chunk in iter(lambda it=iter(iterable): list(islice(it, n)), []):
            yield chunk

    def get_field_info(self, name):
        """Return field information required for the template
        """
        field = self.fields.get(name)
        mode = self.get_field_mode(field)
        html = self.get_field_html(field, mode=mode)
        label = self.get_field_label(field, mode=mode)
        description = self.render_field_description(field, mode=mode)
        required = self.is_field_required(field, mode=mode)
        return {
            "name": name,
            "mode": mode,
            "html": html,
            "field": field,
            "label": label,
            "description": description,
            "required": required,
        }

    def get_field_html(self, field, mode="view"):
        """Render field HTML
        """
        if mode == "view":
            # Lookup custom view adapter
            adapter = queryAdapter(self.context,
                                   interface=IHeaderTableFieldRenderer,
                                   name=field.getName())
            # return immediately if we have an adapter
            if adapter is not None:
                return adapter(field)

        return None

    def get_field_label(self, field, mode="view"):
        """Renders the field label
        """
        widget = self.get_widget(field)
        return getattr(widget, "label", "")

    def render_field_description(self, field, mode="view"):
        """Renders the field description
        """
        widget = self.get_widget(field)
        return getattr(widget, "description", "")

    def render_widget(self, field, mode="view"):
        """Render the field widget
        """
        return self.context.widget(field.getName(), mode=mode)

    def get_field_mode(self, field, default="hidden"):
        """Returns the field mode in the header

        Possible values are:

          - edit: field is rendered in edit mode
          - view: field is rendered in view mode
        """
        mode = "view"
        if field.checkPermission("edit", self.context):
            mode = "edit"
            if not self.is_edit_allowed():
                logger.warn("Permission '{}' granted for the edition of '{}', "
                            "but 'Modify portal content' not granted"
                            .format(field.write_permission, field.getName()))
        elif field.checkPermission("view", self.context):
            mode = "view"

        widget = self.get_widget(field)
        mode_vis = widget.isVisible(self.context, mode=mode, field=field)
        if mode_vis != "visible":
            if mode == "view":
                return default
            # The field cannot be rendered in edit mode, but maybe can be
            # rendered in view mode.
            mode = "view"
            view_vis = widget.isVisible(self.context, mode=mode, field=field)
            if view_vis != "visible":
                return default

        return mode

    def get_widget(self, field):
        """Returns the widget of the field
        """
        if self.is_at_field(field):
            return field.widget
        elif self.is_dx_field(field):
            raise NotImplementedError("DX widgets not yet needed")
        raise TypeError("Field %r is neither a DX nor an AT field")

    def add_status_message(self, message, level="info"):
        """Set a portal status message
        """
        return self.context.plone_utils.addPortalMessage(message, level)

    @viewcache.memoize
    def is_primary_with_partitions(self):
        """Check if the Sample is a primary with partitions
        """
        return IAnalysisRequestWithPartitions.providedBy(self.context)

    def is_primary_bound(self, field):
        """Checks if the field is primary bound
        """
        if not self.is_primary_with_partitions():
            return False
        return getattr(field, "primary_bound", False)

    def is_edit_allowed(self):
        """Check permission 'ModifyPortalContent' on the context
        """
        return check_permission(ModifyPortalContent, self.context)

    def is_field_required(self, field, mode="edit"):
        """Check if the field is required
        """
        if mode == "view":
            return False
        return field.required

    def is_at_field(self, field):
        """Check if the field is an AT field
        """
        return IATField.providedBy(field)

    def is_dx_field(self, field):
        """Check if the field is an DX field
        """
        return IDXField.providedBy(field)

    def can_manage_sample_fields(self):
        """Checks if the user is allowed to manage the sample fields

        TODO: Better use custom permission (same as used for view)
        """
        roles = get_roles()
        if "Manager" in roles:
            return True
        elif "LabManager" in roles:
            return True
        return False