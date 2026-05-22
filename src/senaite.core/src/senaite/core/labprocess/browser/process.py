# -*- coding: utf-8 -*-
import json
import logging

from Products.Five.browser import BrowserView
from bika.lims import api

logger = logging.getLogger(__name__)


class ProcessView(BrowserView):
    """@@process - show one LabProcess pipeline and tasks"""

    def __call__(self):
        self.pipeline_error = None
        self.pipeline = self._get_pipeline()
        self.tasks = self._get_tasks()
        self._service_title_map = self._get_service_title_map()

        return self.index()

    def process_title(self):
        title = api.get_title(self.context) or self.context.getId()
        return title

    def stages(self):
        return self.pipeline or []

    def tasks_count(self):
        return len(self.tasks or [])

    def url_generate_tasks(self):
        return self.context.absolute_url() + "/@@generate-tasks"

    def url_tasks(self):
        return self.context.absolute_url() + "/@@labtasks"

    def url_edit_pipeline(self):
        return self.context.absolute_url() + "/@@pipeline-edit"

    def _get_service_title_map(self):
        mapping = {}
        try:
            portal = api.get_portal()
            cat = getattr(portal, "senaite_catalog_setup", None)
            if cat is None:
                return mapping

            brains = cat(portal_type="AnalysisService")
            for b in brains:
                try:
                    obj = b.getObject()
                    kw = getattr(obj, "getKeyword", None)
                    kw = kw() if callable(kw) else None
                    if not kw:
                        continue
                    kw_u = api.safe_unicode(kw)
                    title = api.get_title(obj) or kw
                    title_u = api.safe_unicode(title)

                    mapping[kw_u] = title_u
                except Exception:
                    continue
        except Exception:
            logger.exception("Failed to build service title map")
        return mapping

    def format_services(self, services):
        out = []
        m = getattr(self, "_service_title_map", {}) or {}

        if services is None:
            services = []
        if isinstance(services, basestring):
            services = [x.strip() for x in services.split(",") if x.strip()]

        for kw in (services or []):
            kw_u = api.safe_unicode((kw or "").strip())
            if not kw_u:
                continue

            title_u = m.get(kw_u)
            if title_u and title_u != kw_u:
                out.append(u"%s (%s)" % (title_u, kw_u))
            else:
                out.append(kw_u)

        return out

    def _get_pipeline_raw(self):
        candidates = [
            "pipeline_stages_configuration",
            "pipeline_stages",
            "pipeline",
            "Pipeline",
            "stages",
            "getPipelineStagesConfiguration",
            "getPipelineStages",
            "getPipeline",
            "getStages",
        ]
        for name in candidates:
            v = getattr(self.context, name, None)
            if v is None:
                continue
            if callable(v):
                try:
                    v = v()
                except Exception:
                    continue
            if v not in (None, ""):
                return v
        return None

    def _get_pipeline(self):
        raw = self._get_pipeline_raw()
        if raw in (None, ""):
            return []

        if isinstance(raw, (list, tuple)):
            return [self._normalize_stage(x) for x in raw if x]
        if isinstance(raw, dict):
            return [self._normalize_stage(raw)]

        try:
            if isinstance(raw, str):
                try:
                    raw = raw.decode("utf-8")
                except Exception:
                    pass

            text = (raw or "").strip()
            if not text:
                return []

            data = json.loads(text)
            if isinstance(data, dict):
                data = [data]
            if not isinstance(data, list):
                self.pipeline_error = "pipeline json is not a list/dict"
                return []
            return [self._normalize_stage(x) for x in data if x]

        except Exception as e:
            self.pipeline_error = "Pipeline parse error: %r" % (e,)
            logger.exception("Failed to parse pipeline")
            return []

    def _normalize_stage(self, d):
        """Normalize one stage dict for template."""
        if not isinstance(d, dict):
            return {"raw": d}

        step = d.get("step") or d.get("Step") or 0
        name = d.get("name") or d.get("stage") or d.get("Stage") or u""
        mode = d.get("mode") or d.get("Mode") or "analysis"
        handler = d.get("handler") or d.get("Handler") or u""
        services = d.get("services") or d.get("service") or []
        users = d.get("users") or d.get("user") or []

        if isinstance(services, basestring):
            services = [x.strip() for x in services.split(",") if x.strip()]
        if isinstance(users, basestring):
            users = [x.strip() for x in users.split(",") if x.strip()]

        if isinstance(handler, (list, tuple)):
            handler = u", ".join([unicode(x) for x in handler if x])
        try:
            mode = (mode or "analysis").strip().lower()
        except Exception:
            mode = "analysis"

        return {
            "step": step,
            "name": name,
            "mode": mode,
            "services": services if isinstance(services, list) else [services],
            "handler": handler or u"",
            "users": users if isinstance(users, list) else [users],
        }

    # Tasks
    def _iter_children(self):
        if hasattr(self.context, "contentValues"):
            return self.context.contentValues()
        if hasattr(self.context, "objectValues"):
            return self.context.objectValues()
        return []

    def _get_tasks(self):
        tasks = []
        for obj in self._iter_children():
            try:
                if api.get_portal_type(obj) != "LabTask":
                    continue
            except Exception:
                continue
            tasks.append(obj)

        def _key(o):
            v = getattr(o, "getStep", None)
            if callable(v):
                try:
                    return int(v() or 999999)
                except Exception:
                    return 999999
            return 999999

        tasks.sort(key=_key)
        return tasks
