# -*- coding: utf-8 -*-
from Products.Archetypes.Widget import StringWidget as BaseStringWidget
from Products.Archetypes.Registry import registerWidget
from AccessControl import ClassSecurityInfo
from DateTime import DateTime

class StringWithHistoryWidget(BaseStringWidget):
    _properties = BaseStringWidget._properties.copy()
    _properties.update({
        'macro': "bika_widgets/string",
    })

    security = ClassSecurityInfo()

    def render(self, instance, field, value, REQUEST=None, **kwargs):
        try:
            base_html = super(StringWithHistoryWidget, self).render(instance, field, value, REQUEST, **kwargs)
            history = getattr(instance, "getFieldHistory", lambda x: [])(field.getName()) or []
            history_html = ""
            for h in history:
                history_html += u"<div class='field-history-entry'>旧值: <i>{}</i> → 新值: <b>{}</b> [{}]</div>".format(
                    h.get("old", ""), h.get("new", ""), h.get("modified", "")
                )
            return base_html + u"<div class='field-history'>%s</div>" % history_html
        except Exception as e:
            import logging
            logging.getLogger("bika.lims").warning("Widget render failed for %s: %s", field.getName(), e)
            return super(StringWithHistoryWidget, self).render(instance, field, value, REQUEST, **kwargs)


registerWidget(StringWithHistoryWidget)
