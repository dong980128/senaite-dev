# -*- coding: utf-8 -*-

from AccessControl import ClassSecurityInfo
from bika.lims import api
from bika.lims import senaiteMessageFactory as _
from plone.supermodel import model
from Products.CMFCore import permissions
from senaite.core.content.base import Container
from senaite.core.content.mixins import ClientAwareMixin
from senaite.core.interfaces import ILabProcessRun
from zope import schema
from zope.interface import implementer


RUN_STATUS_VOCAB = schema.vocabulary.SimpleVocabulary([
    schema.vocabulary.SimpleTerm(value=u"active",title=_(u"Active")),
    schema.vocabulary.SimpleTerm(value=u"pending", title=_(u"Pending")),
    schema.vocabulary.SimpleTerm(value=u"running", title=_(u"Running")),
    schema.vocabulary.SimpleTerm(value=u"done", title=_(u"Done")),
    schema.vocabulary.SimpleTerm(value=u"cancelled", title=_(u"Cancelled")),
])


class ILabProcessRunSchema(model.Schema):
    """运行态：挂在 AR/Sample 下的 Process 实例"""

    template_uid = schema.TextLine(
        title=_(u"Template UID"),
        required=False,
        default=u"",
    )

    ar_uid = schema.TextLine(
        title=_(u"AR UID"),
        required=False,
        default=u"",
    )

    sample_uid = schema.TextLine(
        title=_(u"Sample UID"),
        required=False,
        default=u"",
    )

    status = schema.Choice(
        title=_(u"Status"),
        required=True,
        vocabulary=RUN_STATUS_VOCAB,
        default=u"pending",
    )

    # 第一版：先把 pipeline 保存成 JSON
    pipeline_json = schema.Text(
        title=_(u"Pipeline JSON"),
        required=False,
        default=u"",
        description=_(u"Raw JSON of pipeline stages/tasks"),
    )


@implementer(ILabProcessRun)
class LabProcessRun(Container, ClientAwareMixin):
    """Dexterity Content"""
    security = ClassSecurityInfo()

    @security.protected(permissions.View)
    def getPipeline(self):
        """返回 pipeline 的 list（解析 JSON）"""
        raw = getattr(self, "pipeline_json", u"") or u""
        if not raw:
            return []
        try:
            import json
            return json.loads(raw)
        except Exception:
            return []

    @security.protected(permissions.ModifyPortalContent)
    def setPipeline(self, value):
        """value 可以是 list/dict 或 json string"""
        import json
        if value is None:
            self.pipeline_json = u""
            return
        if isinstance(value, (list, dict)):
            self.pipeline_json = api.safe_unicode(json.dumps(value, ensure_ascii=False))
        else:
            self.pipeline_json = api.safe_unicode(value)
