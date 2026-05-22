# -*- coding: utf-8 -*-

from AccessControl import ClassSecurityInfo
from bika.lims import api
from bika.lims import senaiteMessageFactory as _
from plone.supermodel import model
from Products.CMFCore import permissions
from senaite.core.content.base import Container
from senaite.core.content.mixins import ClientAwareMixin
from senaite.core.interfaces import ILabProcess
from zope import schema
from zope.interface import Invalid, implementer, invariant


class ILabProcessSchema(model.Schema):

    model.fieldset(
        "pipeline",
        label=_(u"Pipeline"),
        fields=[
            "process_key",
            "version",
            "pipeline_stages",
        ]
    )

    title = schema.TextLine(
        title=_(u"Name"),
        required=True,
    )

    description = schema.Text(
        title=_(u"Description"),
        required=False,
    )

    process_key = schema.TextLine(
        title=_(u"Process Keyword"),
        description=_(u"Unique keyword (optional for now)"),
        required=False,
        default=u"",
    )

    version = schema.TextLine(
        title=_(u"Version"),
        required=False,
        default=u"v1.0",
    )

    pipeline_stages = schema.Text(
        title=_(u"Pipeline stages configuration"),
        required=False,
    )

    @invariant
    def validate_process_key_unique(data):
        key = data.process_key
        if not key:
            return

        portal = api.get_portal()
        pc = portal.portal_catalog
        context = getattr(data, "__context__", None)
        if context and getattr(context, "process_key", None) == key:
            return
        brains = pc(portal_type="LabProcess", process_key=api.to_utf8(key))
        if brains:
            raise Invalid(_("Process keyword must be unique"))


@implementer(ILabProcess, ILabProcessSchema)
class LabProcess(Container, ClientAwareMixin):

    security = ClassSecurityInfo()

    @security.protected(permissions.View)
    def getProcessKey(self):
        accessor = self.accessor("process_key")
        return api.to_utf8(accessor(self) or "")

    @security.protected(permissions.ModifyPortalContent)
    def setProcessKey(self, value):
        mutator = self.mutator("process_key")
        mutator(self, api.safe_unicode(value))

    ProcessKey = property(getProcessKey, setProcessKey)

    @security.protected(permissions.View)
    def getPipelineStagesRaw(self):
        accessor = self.accessor("pipeline_stages")
        return accessor(self) or u""

    @security.protected(permissions.View)
    def getPipelineStages(self):
        """Return parsed JSON list; invalid JSON returns []"""
        raw = self.getPipelineStagesRaw()
        if not raw:
            return []
        try:
            import json
            return json.loads(raw)
        except Exception:
            return []
