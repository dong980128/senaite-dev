# -*- coding: utf-8 -*-

import json
import logging

from bika.lims import api
from Products.Five.browser import BrowserView
from zope.interface import alsoProvides
from plone.protect.interfaces import IDisableCSRFProtection

logger = logging.getLogger(__name__)


class GenerateTasksView(BrowserView):
    """Generate LabTask children from pipeline_stages JSON"""

    def __call__(self):
        # allow POST/GET without CSRF for quick testing (optional)
        alsoProvides(self.request, IDisableCSRFProtection)

        process = self.context  # LabProcess
        # field name is pipeline_stages in your schema
        raw = getattr(process, "pipeline_stages", None)

        if raw is None and hasattr(process, "getPipelineStagesRaw"):
            raw = process.getPipelineStagesRaw()

        raw = raw or u""

        try:
            stages = json.loads(raw) if raw.strip() else []
        except Exception as e:
            msg = "Invalid JSON in pipeline_stages: %s" % e
            logger.error(msg)
            return msg

        def to_int(v, default=0):
            try:
                return int(v)
            except Exception:
                return default

        stages = sorted(stages, key=lambda x: to_int(x.get("step", 0)))

        created = 0
        skipped = 0
        updated = 0

        for st in stages:
            step = to_int(st.get("step", 0))
            title = api.safe_unicode(st.get("name") or ("Task %s" % step))
            services = st.get("services") or []
            users = st.get("users") or []

            child_id = "task-%s" % step

            if child_id in process.objectIds():
                # 已存在就更新字段（更适合反复测试）
                obj = process.get(child_id)
                try:
                    obj.title = title
                except Exception:
                    pass

                if hasattr(obj, "step"):
                    obj.step = step
                if hasattr(obj, "stage_name"):
                    obj.stage_name = title
                if hasattr(obj, "services"):
                    obj.services = services
                if hasattr(obj, "assigned_users"):
                    obj.assigned_users = users

                obj.reindexObject()
                updated += 1
                continue

            obj = api.create(process, "LabTask", id=child_id, title=title)
            obj.step = step
            obj.stage_name = title
            obj.services = self._split_csv(services)
            obj.assigned_users = self._split_csv(users)

            obj.reindexObject()
            created += 1

        process.reindexObject()
        return "OK: created=%s, updated=%s, skipped=%s" % (created, updated, skipped)

    def _split_csv(self, v):
        if v is None:
            return []
        if isinstance(v, (list, tuple, set)):
            return list(v)
        if isinstance(v, basestring):
            s = v.strip()
            if not s:
                return []
            if "," in s:
                return [x.strip() for x in s.split(",") if x.strip()]
            return [s]
        return [v]
