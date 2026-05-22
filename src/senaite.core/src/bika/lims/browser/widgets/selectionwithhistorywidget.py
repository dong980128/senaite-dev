# -*- coding: utf-8 -*-

import logging
logger = logging.getLogger()

from Products.Archetypes.Widget import SelectionWidget as BaseSelectionWidget
from Products.Archetypes.Registry import registerWidget
from AccessControl import ClassSecurityInfo
from Products.Archetypes.Field import StringField

from DateTime import DateTime

class SelectionWithHistoryWidget(BaseSelectionWidget):

    _properties = BaseSelectionWidget._properties.copy()
    _properties.update({
        'macro': "selectionwithhistory",
    })

    logger.info("🧩 SelectionWithHistoryWidget 使用宏路径：%s", _properties['macro'])

    security = ClassSecurityInfo()

    def render(self, instance, field, value, REQUEST=None, **kwargs):
        try:
            base_html = super(SelectionWithHistoryWidget, self).render(instance, field, value, REQUEST, **kwargs)

            # 获取历史记录（确保方法存在，防止崩溃）
            get_history = getattr(instance, "getFieldHistory", lambda x: [])
            history = get_history(field.getName()) or []

            history_html = ""
            for h in history:
                history_html += u"<div class='field-history-entry'>旧值: <i>{}</i> → 新值: <b>{}</b> [{}]</div>".format(
                    h.get("old", ""), h.get("new", ""), h.get("modified", "")
                )

            # 返回完整 HTML（字段 + 历史记录）
            return base_html + u"<div class='field-history'>%s</div>" % history_html

        except Exception as e:
            import logging
            logging.getLogger("bika.lims").warning("SelectionWithHistoryWidget render failed for %s: %s",
                                                   field.getName(), e)
            # 最坏情况直接用 super 返回（避免空值导致崩）
            return super(SelectionWithHistoryWidget, self).render(instance, field, value, REQUEST, **kwargs)

registerWidget(SelectionWithHistoryWidget)


def field_getHistory(self, instance):
    """供 .pt 模板调用：field.getHistory(instance)"""
    fieldname = self.getName()
    logger.info("[WIDGET] 获取字段历史: %s", fieldname)
    return instance.getFieldHistory(fieldname)


StringField.getHistory = field_getHistory
