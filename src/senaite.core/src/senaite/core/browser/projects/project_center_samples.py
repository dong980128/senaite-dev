# -*- coding: utf-8 -*-
import logging
from collections import defaultdict

from Products.Five import BrowserView
from Products.Five.browser.pagetemplatefile import ViewPageTemplateFile
from zope.interface import implementer
from zope.publisher.interfaces.browser import IPublishTraverse
from zExceptions import NotFound
from plone import api
from Products.CMFPlone.utils import safe_unicode
from urllib import quote_plus

LOGGER = logging.getLogger("senaite.core")


def _idxlist(catalog):
    try:
        return list(catalog.indexes())
    except Exception:
        try:
            return list(catalog.indexes.keys())
        except Exception:
            return []


def _has_index(catalog, name):
    return name in _idxlist(catalog)


def _get_tool(tool_id):
    try:
        return api.portal.get_tool(tool_id)
    except Exception:
        return None


def _available_catalogs(ids):
    out = []
    for cid in ids:
        cat = _get_tool(cid)
        if cat:
            out.append(cat)
    return out


def _search_catalogs(catalogs, **query):
    seen = set()
    results = []
    for cat in catalogs:
        try:
            brains = cat(**query)
        except TypeError:
            brains = cat.searchResults(**query)
        for b in brains:
            p = b.getPath()
            if p in seen:
                continue
            seen.add(p)
            results.append(b)
    return results


@implementer(IPublishTraverse)
class ProjectCenterSamplesView(BrowserView):
    """URL: /@@project_center_samples/<center_uid>
       按“中心 → 项目 → 样本数”逐行显示
    """

    index = ViewPageTemplateFile("templates/project_center_sample.pt")
    center_uid = None
    _center = None

    def publishTraverse(self, request, name):
        if self.center_uid is None:
            self.center_uid = name
            return self
        raise NotFound(self.context, name, request)

    def __call__(self):
        # 没 uid 就 404，和 Plone/resolveuid 行为一致
        if not self.center_uid:
            raise NotFound(self.context, "project_center_samples", self.request)
        return self.index()

    # 页面标题
    def title(self):
        c = self._get_center()
        center_title = safe_unicode(c.Title()) if c else u"中心"
        return u"%s - 项目样本数" % center_title

    # 供模板取中心对象
    def center(self):
        return self._get_center()

    # ----------------- 解析中心对象 -----------------
    def _get_center(self):
        if self._center is not None:
            return self._center

        uid = self.center_uid
        if not uid:
            return None

        try:
            obj = api.content.get(UID=uid)
            if obj:
                self._center = obj
                return obj
        except Exception:
            pass

        # 在系统中的 client目录里找
        client_cats = _available_catalogs(["senaite_catalog_client", "portal_catalog"])
        for cat in client_cats:
            try:
                brains = cat.unrestrictedSearchResults(UID=uid)
            except Exception:
                brains = cat.searchResults(UID=uid)
            if brains:
                obj = getattr(brains[0], "_unrestrictedGetObject", None)
                obj = obj() if obj else brains[0].getObject()
                self._center = obj
                return obj

        if "/" in uid:
            obj = api.content.get(path=uid)
            if obj:
                self._center = obj
                return obj

        LOGGER.error("[PCS] Could not resolve center for uid=%s. Reindex Client(s) or verify UID.", uid)
        return None

    # ----------------- 生成“项目 → 样本数”行 -----------------
    def rows(self):
        center = self._get_center()
        if not center:
            return []

        center_path = "/".join(center.getPhysicalPath())

        # 可能的样本/AR 目录（按优先级）
        ar_cats = _available_catalogs([
            "senaite_catalog_sample",
            "senaite_catalog_analysisrequest",
            "senaite_catalog",
            "portal_catalog",
        ])
        if not ar_cats:
            return []

        # 先按 getClientUID 过滤
        types = ["AnalysisRequest", "Sample"]
        ar_brains = []
        if _has_index(ar_cats[0], "getClientUID"):
            ar_brains = _search_catalogs(ar_cats,
                                         portal_type=types,
                                         getClientUID=self.center_uid)

        # 若为空，回退到“路径 + 类型”过滤
        if not ar_brains:
            ar_brains = _search_catalogs(
                ar_cats,
                portal_type=types,
                path={"query": center_path, "depth": 6}
            )

        if not ar_brains:
            return []

        # 汇总：优先使用样本/AR上的 ProjectName；其次才用 Project 相关字段；没有则归为“未指定项目”
        counts = defaultdict(int)
        meta = {}  # key -> {uid, title}

        def _brain_text(v):
            if isinstance(v, (list, tuple)):
                v = u", ".join([safe_unicode(x) for x in v if x])
            return safe_unicode(v or u"").strip()

        for b in ar_brains:
            # 先从brain的元数据列拿
            proj_name = (
                    getattr(b, "ProjectName", None) or
                    getattr(b, "getProjectName", None) or
                    getattr(b, "project_name", None)
            )

            # 取不到再从对象上拿
            if not proj_name:
                try:
                    obj = getattr(b, "_unrestrictedGetObject", None)
                    obj = obj() if obj else b.getObject()
                    getter = getattr(obj, "getProjectName", None)
                    proj_name = getter() if callable(getter) else getattr(obj, "ProjectName", None)
                except Exception:
                    proj_name = None

            proj_name = _brain_text(proj_name)

            if not proj_name:
                legacy_title = (
                        getattr(b, "getProjectTitle", None) or
                        getattr(b, "Project", None) or
                        getattr(b, "project", None)
                )
                proj_name = _brain_text(legacy_title)

            # UID 只用于生成可点击链接；显示与分组一律用 proj_name
            proj_uid = (
                    getattr(b, "getProjectUID", None) or
                    getattr(b, "projectUID", None) or
                    ""
            )

            key = proj_name or u"(未指定项目)"
            LOGGER.debug(u"[PCS] center=%s, ProjectName=%r, proj_uid=%s",
                         safe_unicode(center.Title()), key, proj_uid)
            counts[key] += 1
            if key not in meta:
                meta[key] = {"uid": proj_uid, "title": key}

        proj_cats = _available_catalogs(["senaite_catalog", "portal_catalog"])
        rows = []
        for key, m in meta.items():
            uid = m["uid"]
            title = m["title"]
            url = None
            if uid and proj_cats:
                p = _search_catalogs(proj_cats, UID=uid)
                if p:
                    url = p[0].getURL()
                    if not title:
                        title = p[0].Title or title

            portal = api.portal.get()
            base = portal.absolute_url() + "/projects/project-center-samples"

            # center_uid
            c_q = quote_plus((self.center_uid or u"").encode("utf-8") if isinstance(self.center_uid, unicode)
                             else (self.center_uid or u""))

            uid = m.get("uid") or u""  # 项目UID
            title = m.get("title") or u"(未指定项目)"  # 展示标题

            p_uid_q = quote_plus(uid.encode("utf-8")) if uid else u""
            p_title_q = quote_plus(title.encode("utf-8") if isinstance(title, unicode) else title)

            if p_uid_q:
                list_url = u"{}?client_uid={}&project_uid={}&project={}&samples_review_state=all".format(
                    base, c_q, p_uid_q, p_title_q
                )
            else:
                list_url = u"{}?client_uid={}&project={}&samples_review_state=all".format(
                    base, c_q, p_title_q
                )

            rows.append({
                "center_title": safe_unicode(center.Title()),
                "project_title": safe_unicode(title or u"(未指定项目)"),
                "project_url": url,
                "sample_count": counts[key],
                "list_url": list_url,
            })

        rows.sort(key=lambda r: (r["project_title"] or u""))
        return rows
