# -*- coding: utf-8 -*-
import logging
from plone.app.i18n.locales.browser.selector import LanguageSelector as Base
from Products.Five.browser.pagetemplatefile import ViewPageTemplateFile

logger = logging.getLogger("senaite.core.languageselector")
logger.info("LanguageSelector module loaded from: %s", __file__)

class LanguageSelector(Base):
    template = ViewPageTemplateFile("templates/languageselector.pt")

    def __init__(self, context, request, view, manager):
        super(LanguageSelector, self).__init__(context, request, view, manager)

    def update(self):
        super(LanguageSelector, self).update()
