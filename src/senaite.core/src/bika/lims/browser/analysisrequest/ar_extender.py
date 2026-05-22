# -*- coding: utf-8 -*-

import logging
from zope.component import adapter
from zope.interface import implementer
from plone.z3cform.fieldsets.extensible import FormExtender
from plone.z3cform.fieldsets.interfaces import IFormExtender
from z3c.form.interfaces import HIDDEN_MODE
from bika.lims.interfaces.analysisrequest import IEditAnalysisRequest

logger = logging.getLogger("senaite.ar_extender")

@adapter(IEditAnalysisRequest)
@implementer(IFormExtender)
class HideFieldsAfterReceived(FormExtender):
    def update(self):
        obj = self.context
        wf_state = obj.portal_workflow.getInfoFor(obj, "review_state", default="")

        logger.info("+++ Extender triggered +++")
        logger.info("+++ Current AR state: %s", wf_state)

        if wf_state != "sample_received":
            return

        fields_to_hide = [
            "ShippingBoxUsed",
            "TemperatureNormal",
            "StorageCondition",
            "DeliveryTime",
        ]

        for fname in fields_to_hide:
            if fname in self.form.fields:
                logger.info("+++ Hiding field: %s", fname)
                self.form.fields[fname].mode = HIDDEN_MODE
