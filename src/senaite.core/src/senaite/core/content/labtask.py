# -*- coding: utf-8 -*-

from AccessControl import ClassSecurityInfo
from bika.lims import api
from bika.lims import senaiteMessageFactory as _
from plone.supermodel import model
from Products.CMFCore import permissions
from senaite.core.content.base import Item
from senaite.core.content.mixins import ClientAwareMixin
from senaite.core.interfaces import ILabTask
from zope import schema
from zope.interface import Invalid, implementer, invariant


TASK_STATUS_VOCAB = schema.vocabulary.SimpleVocabulary([
    schema.vocabulary.SimpleTerm(value=u"pending", title=_(u"Pending")),
    schema.vocabulary.SimpleTerm(value=u"assigned", title=_(u"Assigned")),
    schema.vocabulary.SimpleTerm(value=u"in_progress", title=_(u"In progress")),
    schema.vocabulary.SimpleTerm(value=u"done", title=_(u"Done")),
    schema.vocabulary.SimpleTerm(value=u"reviewed", title=_(u"Reviewed")),
    schema.vocabulary.SimpleTerm(value=u"blocked", title=_(u"Blocked")),
])


class ILabTaskSchema(model.Schema):

    model.fieldset(
        "task",
        label=_(u"Task"),
        fields=[
            "task_key",
            "step",
            "stage_name",
            "task_status",
            "services",
        ]
    )

    model.fieldset(
        "assignment",
        label=_(u"Assignment"),
        fields=[
            "assigned_users",
            "department",
            "due_date",
        ]
    )

    model.fieldset(
        "links",
        label=_(u"Links"),
        fields=[
            "sample_uid",
        ]
    )

    model.fieldset(
        "audit",
        label=_(u"Audit"),
        fields=[
            "started_at",
            "finished_at",
            "reviewed_at",
            "result_summary",
        ]
    )

    title = schema.TextLine(title=_(u"Name"), required=True)
    description = schema.Text(title=_(u"Description"), required=False)

    task_key = schema.TextLine(
        title=_(u"Task Keyword"),
        description=_(u"Unique keyword (optional for now)"),
        required=False,
        default=u"",
    )

    step = schema.Int(title=_(u"Step"), required=False, min=0)

    stage_name = schema.TextLine(title=_(u"Stage name"), required=False, default=u"")

    task_status = schema.Choice(
        title=_(u"Status"),
        vocabulary=TASK_STATUS_VOCAB,
        required=True,
        default=u"pending",
    )

    assigned_users = schema.List(
        title=_(u"Assigned users"),
        description=_(u"Store Plone user ids, e.g. user_lab_1"),
        value_type=schema.TextLine(),
        required=False,
        default=[],
    )

    department = schema.TextLine(title=_(u"Department"), required=False, default=u"")

    due_date = schema.Datetime(title=_(u"Due date"), required=False)

    # A 方案：存 service keyword
    services = schema.List(
        title=_(u"Services (keywords)"),
        description=_(u"Store service keywords, e.g. SC_TCR_LIB"),
        value_type=schema.TextLine(),
        required=False,
        default=[],
    )

    sample_uid = schema.TextLine(
        title=_(u"Sample UID"),
        description=_(u"Bind to a Sample/AR UID when running as an instance"),
        required=False,
        default=u"",
    )

    started_at = schema.Datetime(title=_(u"Started at"), required=False)
    finished_at = schema.Datetime(title=_(u"Finished at"), required=False)
    reviewed_at = schema.Datetime(title=_(u"Reviewed at"), required=False)

    result_summary = schema.Text(title=_(u"Result summary"), required=False)

    @invariant
    def validate_task_key_unique(data):
        key = data.task_key
        if not key:
            return
        portal = api.get_portal()
        pc = portal.portal_catalog
        context = getattr(data, "__context__", None)
        if context and getattr(context, "task_key", None) == key:
            return
        brains = pc(portal_type="LabTask", task_key=api.to_utf8(key))
        if brains:
            raise Invalid(_("Task keyword must be unique"))


@implementer(ILabTask, ILabTaskSchema)
class LabTask(Item, ClientAwareMixin):

    security = ClassSecurityInfo()

    @security.protected(permissions.View)
    def getTaskKey(self):
        accessor = self.accessor("task_key")
        return api.to_utf8(accessor(self) or "")

    @security.protected(permissions.ModifyPortalContent)
    def setTaskKey(self, value):
        mutator = self.mutator("task_key")
        mutator(self, api.safe_unicode(value))

    TaskKey = property(getTaskKey, setTaskKey)
