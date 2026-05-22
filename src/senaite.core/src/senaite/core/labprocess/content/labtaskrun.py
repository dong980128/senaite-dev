# -*- coding: utf-8 -*-

from AccessControl import ClassSecurityInfo
from bika.lims import api
from bika.lims import senaiteMessageFactory as _
from plone.supermodel import model
from Products.CMFCore import permissions
from senaite.core.content.base import Item
from senaite.core.content.mixins import ClientAwareMixin
from senaite.core.interfaces import ILabTaskRun
from zope import schema
from zope.interface import implementer

TASK_STATUS_VOCAB = schema.vocabulary.SimpleVocabulary([
    schema.vocabulary.SimpleTerm(value=u"pending", title=_(u"Pending")),
    schema.vocabulary.SimpleTerm(value=u"running", title=_(u"Running")),
    schema.vocabulary.SimpleTerm(value=u"done", title=_(u"Done")),
    schema.vocabulary.SimpleTerm(value=u"retracted", title=_(u"Retracted")),
])

TASK_MODE_VOCAB = schema.vocabulary.SimpleVocabulary([
    schema.vocabulary.SimpleTerm(value=u"analysis", title=_(u"Analysis")),
    schema.vocabulary.SimpleTerm(value=u"custom", title=_(u"Custom")),
])


class ILabTaskRunSchema(model.Schema):
    step = schema.Int(title=_(u"Step"), required=False, default=0)
    stage_name = schema.TextLine(title=_(u"Stage"), required=False, default=u"")
    task_name = schema.TextLine(title=_(u"Task Name"), required=True)

    services = schema.List(
        title=_(u"Services"),
        required=False,
        value_type=schema.TextLine(),
        default=[],
    )

    assigned_users = schema.List(
        title=_(u"Users"),
        required=False,
        value_type=schema.TextLine(),
        default=[],
    )

    status = schema.Choice(
        title=_(u"Status"),
        required=True,
        vocabulary=TASK_STATUS_VOCAB,
        default=u"pending",
    )

    processrun_uid = schema.TextLine(
        title=_(u"ProcessRun UID"),
        required=False,
        default=u"",
    )

    mode = schema.Choice(
        title=_(u"Mode"),
        vocabulary=TASK_MODE_VOCAB,
        required=True,
        default=u"analysis",
    )

    handler = schema.TextLine(
        title=_(u"Handler"),
        required=False,
        default=u"",
    )

    analysis_uids = schema.List(
        title=_(u"Analysis UIDs"),
        required=False,
        value_type=schema.TextLine(),
        default=[]
    )

    results_snapshot_json = schema.Text(
        title=_(u"Results Snapshot JSON"),
        required=False,
        default=u""
    )

    completed_by = schema.TextLine(
        title=_(u"Completed By"),
        required=False,
        default=u""
    )


@implementer(ILabTaskRun)
class LabTaskRun(Item, ClientAwareMixin):
    security = ClassSecurityInfo()

    @security.protected(permissions.View)
    def getServicesText(self):
        vals = getattr(self, "services", []) or []
        if isinstance(vals, basestring):
            return vals
        return u", ".join([api.safe_unicode(x) for x in vals])

    @security.protected(permissions.View)
    def getUsersText(self):
        vals = getattr(self, "assigned_users", []) or []
        if isinstance(vals, basestring):
            return vals
        return u", ".join([api.safe_unicode(x) for x in vals])

    @security.protected(permissions.View)
    def getMode(self):
        return api.safe_unicode(getattr(self, "mode", u"analysis") or u"analysis").lower()

    @security.protected(permissions.View)
    def getHandler(self):
        return api.safe_unicode(getattr(self, "handler", u"") or u"")

    @security.protected(permissions.View)
    def getReviewState(self):
        wf = api.get_tool("portal_workflow")
        state = wf.getInfoFor(self, "review_state", default="")
        return api.safe_unicode(state or u"").lower()

    @security.protected(permissions.View)
    def getEffectiveStatus(self):
        rs = self.getReviewState()
        if rs:
            return rs
        return api.safe_unicode(getattr(self, "status", u"") or u"").lower()
