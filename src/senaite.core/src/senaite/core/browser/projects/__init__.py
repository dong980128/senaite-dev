# -*- coding: utf-8 -*-

import logging
from urlparse import parse_qs, urlparse

from Products.Five.browser import BrowserView
from bika.lims import api
from urllib import quote, unquote  # Py2
from senaite.core.browser.samples.view import SamplesView

logger = logging.getLogger("senaite.core.projectcentersample")


def _sample_catalog():
    try:
        from senaite.core.catalog import SAMPLE_CATALOG
    except Exception:
        SAMPLE_CATALOG = "senaite_catalog_samples"
    return api.get_tool(SAMPLE_CATALOG) or api.get_tool("portal_catalog")


def get_project_name(brain):
    for attr in ("ProjectName", "getProjectName", "project_name"):
        val = getattr(brain, attr, None)
        if val:
            return api.safe_unicode(val)
    try:
        obj = brain.getObject()
        getter = getattr(obj, "getProjectName", None)
        if callable(getter):
            return api.safe_unicode(getter())
        field = getattr(obj, "getField", lambda *a, **k: None)("ProjectName")
        if field:
            return api.safe_unicode(field.get(obj))
        return api.safe_unicode(getattr(obj, "ProjectName", u""))
    except Exception:
        return u""


def get_center_info(brain):
    title = (getattr(brain, "getClientTitle", None) or
             getattr(brain, "ClientTitle", None) or u"")
    cid = (getattr(brain, "getClientID", None) or
           getattr(brain, "ClientID", None) or u"")
    uid = (getattr(brain, "getClientUID", None) or
           getattr(brain, "ClientUID", None) or
           getattr(brain, "client_uid", None))
    if title and cid and uid:
        return api.safe_unicode(title), api.safe_unicode(cid), api.safe_unicode(uid)
    try:
        obj = brain.getObject()
        client = getattr(obj, "getClient", lambda: None)()
        if client:
            title = title or client.Title()
            if not cid:
                getter = getattr(client, "getClientID", None)
                cid = getter() if callable(getter) else getattr(client, "ClientID", u"")
            uid = uid or client.UID()
    except Exception:
        pass
    return api.safe_unicode(title or u""), api.safe_unicode(cid or u""), api.safe_unicode(uid or u"")


# ------- 项目首页 -------
class ProjectsByNameView(BrowserView):

    def __call__(self):
        self.title = u"项目"
        self.rows = self._build_rows()
        return self.index()

    def _iter_ar_brains(self):
        cat = _sample_catalog()
        brains = list(cat.searchResults(portal_type="AnalysisRequest"))
        if not brains:
            brains = list(cat.searchResults(portal_type="Sample"))
        return brains

    def _build_rows(self):
        grouped = {}
        for b in self._iter_ar_brains():
            pname = get_project_name(b)
            if not pname:
                continue
            _title, _cid, uid = get_center_info(b)
            entry = grouped.setdefault(pname, {"total": 0, "center_uids": set()})
            entry["total"] += 1
            if uid:
                entry["center_uids"].add(uid)
        rows = []
        for pname, info in sorted(grouped.items(), key=lambda kv: api.safe_unicode(kv[0])):
            rows.append({"pname": pname, "centers": len(info["center_uids"]), "total": info["total"]})
        return rows

    def project_total_href(self, pname):
        base = self.context.absolute_url() + "/project-center-samples"
        return u"{}?project={}&samples_review_state=all".format(
            base,
            quote(api.safe_unicode(pname).encode("utf-8"))
        )


class ProjectCentersView(BrowserView):
    def __call__(self):
        pname = api.safe_unicode(self.request.get("name", u""))
        if not pname:
            return u"<div class='portalMessage error'>缺少 ProjectName</div>"

        rows = self._rows(pname)

        html = [
            u"<table class='listing' style='margin:6px 0'>",
            u"<thead><tr><th>中心名称</th><th>中心编号</th><th>样本数量</th></tr></thead><tbody>"
        ]

        base = self.context.absolute_url() + "/project-center-samples"

        for r in rows:
            center_title = api.safe_unicode(r.get("center_title", u""))
            center_id = api.safe_unicode(r.get("center_id", u""))
            client_uid = api.safe_unicode(r.get("client_uid", u""))
            total = int(r.get("total", 0))

            href = u"{base}?project={p}&name={p}&samples_review_state=all".format(
                base=api.safe_unicode(base),
                p=quote(api.safe_unicode(pname).encode("utf-8")),
            )
            if client_uid:
                href += u"&client_uid={}".format(quote(client_uid.encode("utf-8")))
            if center_id:
                href += u"&client_id={}".format(quote(center_id.encode("utf-8")))

            html.append(
                u"<tr>"
                u"<td><a class='go-samples' href='{h}'>{title}</a></td>"
                u"<td><a class='go-samples' href='{h}'>{cid}</a></td>"
                u"<td style='text-align:center;'><a class='go-samples' href='{h}'>{total}</a></td>"
                u"</tr>".format(
                    h=href,
                    title=center_title,
                    cid=center_id,
                    total=total,
                )
            )

        html.append(u"</tbody></table>")
        return u"".join(html)

    def _rows(self, pname):
        cat = _sample_catalog()
        brains = list(cat.searchResults(portal_type="AnalysisRequest")) or \
                 list(cat.searchResults(portal_type="Sample"))

        # 只保留本项目
        filtered = [b for b in brains if get_project_name(b) == pname]

        # 按“中心名称|中心编号”聚合，同时保存 client_uid
        grouped = {}
        for b in filtered:
            title, cid, uid = get_center_info(b)  # (中心名称, 中心编号, client_uid)
            key = (title or u"") + u"|" + (cid or u"")
            data = grouped.setdefault(key, {
                "center_title": title,
                "center_id": cid,
                "client_uid": uid,
                "total": 0
            })
            data["total"] += 1

        # 按中心名称排序
        return sorted(
            grouped.values(),
            key=lambda r: api.safe_unicode(r.get("center_title") or u"")
        )


class ProjectCenterSamplesView(SamplesView):

    def _load_params(self):
        req = self.request

        def _get(key, default=u""):
            v = None
            try:
                v = req.form.get(key, None)
            except Exception:
                v = None

            if not v:
                try:
                    v = req.get(key, None)
                except Exception:
                    v = None

            if not v:
                qs = None
                try:
                    qs = req.environ.get("QUERY_STRING", None)
                except Exception:
                    qs = None
                if not qs:
                    try:
                        qs = req.get("QUERY_STRING", None)
                    except Exception:
                        qs = None
                if qs:
                    qd = parse_qs(qs, keep_blank_values=True)
                    lst = qd.get(key, []) or qd.get(key.encode("utf-8"), [])
                    v = (lst[0] if lst else None)

            if not v:
                ref = None
                try:
                    ref = req.environ.get("HTTP_REFERER", None)
                except Exception:
                    ref = None
                if not ref:
                    try:
                        ref = req.get_header("referer", None)
                    except Exception:
                        ref = None

                if ref:
                    parsed = urlparse(ref)
                    qd = parse_qs(parsed.query, keep_blank_values=True)

                    frag = (parsed.fragment or u"")
                    if frag.startswith("?"):
                        frag = frag[1:]
                    if frag:
                        fd = parse_qs(frag, keep_blank_values=True)
                        qd.update(fd)

                    lst = qd.get(key, []) or qd.get(key.encode("utf-8"), [])
                    v = (lst[0] if lst else None)

            if v is None:
                v = default

            return api.safe_unicode(unquote(v)).strip()

        # project 可能叫 project 或 name，两者择一
        self.project = (_get("project") or _get("name") or u"").strip()
        self.client_uid = _get("client_uid", u"")
        self.client_id = _get("client_id", u"")

    def update(self):
        self._load_params()
        pname = self.project or u""
        self.title = u"{}".format(pname, pname)
        return super(ProjectCenterSamplesView, self).update()

    def get_catalog_query(self, *args, **kwargs):
        self._load_params()
        q = super(ProjectCenterSamplesView, self).get_catalog_query(*args, **kwargs)

        # 中心过滤：优先 UID，否则 ID
        if self.client_uid:
            q["getClientUID"] = self.client_uid
        elif self.client_id:
            q["getClientID"] = self.client_id

        # 项目过滤：只写“存在的索引”
        proj = (self.project or u"").strip()
        if proj:
            try:
                cat = api.get_tool("senaite_catalog_sample")
                idxs = set(cat.indexes() or [])
            except Exception:
                idxs = set()

            candidates = (
                "getProjectName",
                "getProject",
                "Project",
                "getProjectTitle",
            )

            for k in candidates:
                if k in idxs:
                    q[k] = proj
                    break
        return q

    def folderitems(self):
        self._load_params()
        items = super(ProjectCenterSamplesView, self).folderitems()

        proj = (self.project or u"").strip()
        cuid = (self.client_uid or u"").strip()
        cid = (self.client_id or u"").strip()

        if not (proj or cuid or cid):
            return items

        def _uid_of(it):
            return it.get("uid") or it.get("UID") or it.get("obj_uid") or it.get("ObjUID")

        def _load_obj(it):
            uid = _uid_of(it)
            if not uid:
                return None
            try:
                return api.get_object_by_uid(uid, default=None)
            except Exception:
                return None

        filtered = []
        for it in items:
            if not it:
                continue

            obj = None

            # 项目过滤
            if proj:
                name = u""
                # 从行字段拿
                for k in ("getProjectName", "ProjectName",):
                    v = it.get(k, None)
                    if v:
                        name = api.safe_unicode(v).strip()
                        break

                # 行里拿不到，再读对象
                if not name:
                    obj = _load_obj(it)
                    fn = getattr(obj, "getProjectName", None) if obj else None
                    if callable(fn):
                        try:
                            name = api.safe_unicode(fn() or u"").strip()
                        except Exception:
                            name = u""

                if name != proj:
                    continue

            # 中心过滤
            if cuid or cid:
                row_uid = api.safe_unicode(it.get("ClientUID") or u"").strip()
                row_cid = api.safe_unicode(it.get("ClientID") or u"").strip()

                # 行字段不匹配 -> 再用对象兜底核对
                if (cuid and row_uid != cuid) or (cid and row_cid != cid):
                    if obj is None:
                        obj = _load_obj(it)

                    u_ok, i_ok = True, True
                    if cuid:
                        try:
                            u_ok = (obj is not None and api.safe_unicode(obj.getClientUID()) == cuid)
                        except Exception:
                            u_ok = False
                    if cid:
                        try:
                            i_ok = (obj is not None and api.safe_unicode(obj.getClientID()) == cid)
                        except Exception:
                            i_ok = False

                    if not (u_ok and i_ok):
                        continue
            filtered.append(it)

        return filtered
