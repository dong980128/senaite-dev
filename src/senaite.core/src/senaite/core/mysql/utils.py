# -*- coding: utf-8 -*-
from __future__ import absolute_import

import logging
from urllib import quote

from bika.lims import api
from Products.CMFPlone.utils import safe_unicode
from plone.memoize import view

logger = logging.getLogger("senaite.core.mysql.resolver")

def _norm(v):
    """Normalize to safe unicode str (py2 compatible)."""
    try:
        return safe_unicode(v or u"").strip()
    except Exception:
        try:
            return safe_unicode(str(v)).strip()
        except Exception:
            return u""


class BaseResolver(object):
    """Common base: always has self.request for plone.memoize.view.memoize"""

    def __init__(self, request=None):
        self.request = request or api.get_request()

    @view.memoize
    def _portal(self):
        return api.get_portal()

    @view.memoize
    def _site_base(self):
        portal = self._portal()
        return "/".join(portal.getPhysicalPath())

class CenterResolver(BaseResolver):

    @view.memoize
    def _title2id(self):
        portal = self._portal()
        clients = portal.get("clients", None)
        title2id = {}
        if clients is None:
            return title2id

        for oid in clients.objectIds():
            try:
                obj = clients[oid]
                title = _norm(obj.Title() or u"")
                if title:
                    title2id[title] = oid
            except Exception:
                continue

        return title2id

    def client_info_by_center_name(self, center_name):
        name = _norm(center_name)
        if not name:
            return (None, None, None)

        oid = self._title2id().get(name)
        if not oid:
            return (None, None, None)

        base = self._site_base()
        url = base + "/clients/" + oid

        samples_url = url + "/analysisrequests#?samples_review_state=all"
        return (oid, url, samples_url)


class SubjectResolver(BaseResolver):

    @view.memoize
    def _sample_catalog(self):
        """Get sample catalog if available."""
        for name in ("senaite_catalog_sample",):
            try:
                cat = api.get_tool(name)
                if cat:
                    return cat
            except Exception:
                continue
        return None

    def subject_info_by_sample_id(self, sample_id, verify=False):

        sid = _norm(sample_id)
        if not sid:
            return (None, None)

        subject_uid = sid
        subject_url = self._site_base() + "/subjects/@@subject?uid=" + quote(subject_uid)

        if not verify:
            return (subject_uid, subject_url)

        cat = self._sample_catalog()
        if cat is None:
            return (subject_uid, subject_url)

        try:
            brains = cat({"getSubjectUID": subject_uid})
            if brains:
                return (subject_uid, subject_url)
        except Exception:
            # 校验失败不影响主流程：仍返回可跳转链接
            pass

        return (subject_uid, subject_url)
