# -*- coding: utf-8 -*-
from Products.Archetypes.Widget import StringWidget
from Products.Archetypes.Registry import registerWidget
import logging

logger = logging.getLogger("senaite.lims.view")


class MyStringWidget(StringWidget):
    _properties = StringWidget._properties.copy()
    _properties.update({
        'macro': 'bika_widgets/mystring',
    })

    def getOldValue(self, context):
        try:
            snapshot = getattr(context, "OldFieldSnapshot", [])
            field = getattr(self, "field", None)
            if not snapshot or not field:
                return []

            field_name = field.getName().strip()
            return [
                entry for entry in snapshot
                if str(entry.get("field", "")).strip() == field_name
            ]
        except Exception as e:
            logger.error("[WIDGET] 获取字段旧值失败：%s", str(e))
            return []


registerWidget(MyStringWidget)
