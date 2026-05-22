# -*- coding: utf-8 -*-
import json
import logging
from bika.lims import api
from Products.Five.browser import BrowserView
from Products.CMFCore import permissions
from AccessControl import ClassSecurityInfo

logger = logging.getLogger("senaite.core.labprocess")


class PipelineEditView(BrowserView):
    security = ClassSecurityInfo()
    security.declareProtected(permissions.ModifyPortalContent, "__call__")

    def get_rows(self):
        """Read JSON from pipeline_stages -> list[dict] (兼容旧数据)"""
        raw = getattr(self.context, "pipeline_stages", "") or "[]"
        try:
            rows = json.loads(raw) or []
        except Exception:
            rows = []

        # 兼容旧数据：补默认值
        for r in rows:
            r.setdefault("mode", "analysis")
            r.setdefault("handler", "")
            r.setdefault("services", "")
            r.setdefault("users", "")
            r.setdefault("name", "")
            r.setdefault("step", "")
            r.setdefault("conditional_services", [])

        if not rows:
            rows = [{
                "step": "",
                "name": "",
                "mode": "analysis",
                "service": "",
                "handler": "",
                "user": "",
            }]

        return rows

    def conditional_to_text(self, conditional_services):
        """把 conditional_services list 转成文本格式供 textarea 显示"""
        if not conditional_services:
            return u""
        lines = []
        for cs in (conditional_services or []):
            svc = cs.get("service", "")
            field = cs.get("condition_field", "")
            val = cs.get("condition_value", "")
            trigger = cs.get("trigger_service", "")
            if svc and field and val:
                lines.append(u"%s:%s=%s" % (svc, field, val))
        return u"\n".join(lines)

    def get_services_options(self):
        portal = api.get_portal()
        cat = getattr(portal, "senaite_catalog_setup", None)
        if cat is None:
            logger.warn("[get_services_options] senaite_catalog_setup not found")
            return []

        brains = cat(portal_type="AnalysisService", sort_on="sortable_title")

        out = []
        for b in brains:
            try:
                obj = b.getObject()
            except Exception:
                continue

            try:
                kw = obj.getKeyword()
            except Exception:
                kw = obj.getId()

            out.append({
                "keyword": api.safe_unicode(kw),
                "title": api.safe_unicode(obj.Title() or kw),
            })

        return out

    def __call__(self):
        req = self.request
        if req.get("form.submitted"):
            rows_json = req.get("rows_json", "[]") or "[]"
            try:
                rows = json.loads(rows_json) or []
            except Exception:
                rows = []

            cleaned = []
            for row in rows:
                step = (row.get("step") or "").strip()
                name = (row.get("name") or "").strip()
                users = (row.get("users") or "").strip()

                mode = (row.get("mode") or "analysis").strip()  # analysis/custom
                handler = (row.get("handler") or "").strip()

                # services 可能来自多选：list，也可能是字符串
                services = row.get("services") or ""
                if isinstance(services, (list, tuple)):
                    services = ",".join([s.strip() for s in services if s and s.strip()])
                services = services.strip()
                if mode not in ("analysis", "custom"):
                    mode = "analysis"

                if mode == "analysis":
                    if not services:
                        logger.warn("Stage missing services: step=%s name=%s", step, name)
                else:
                    # custom 不用 services（可清空避免误用）
                    services = ""
                    # custom 建议必须有 handler（否则不知道跳哪里）
                    if not handler:
                        logger.warn("Custom stage missing handler: step=%s name=%s", step, name)

                # 解析 conditional_services
                conditional_services_raw = row.get("conditional_services") or []

                # 兼容字符串和列表两种格式
                if isinstance(conditional_services_raw, basestring):
                    conditional_services_raw = [conditional_services_raw]

                conditional_services = []
                for item in conditional_services_raw:
                    if isinstance(item, dict):
                        # 已经是 dict 直接用，但检查 condition_value 是否混入了 trigger_service
                        val = item.get("condition_value", "")
                        if ":" in val:
                            parts = val.split(":", 1)
                            item["condition_value"] = parts[0].strip()
                            if not item.get("trigger_service"):
                                item["trigger_service"] = parts[1].strip()
                        conditional_services.append(item)
                    elif isinstance(item, basestring):
                        # 文本格式: sc_library_prep:result_build_library=yes:sc_suspension_prep_qc
                        item = item.strip()
                        if not item:
                            continue
                        parts = item.split(":")
                        if len(parts) < 2:
                            continue
                        svc = parts[0].strip()
                        trigger = parts[2].strip() if len(parts) > 2 else ""
                        fv = parts[1].strip()  # result_build_library=yes
                        if "=" not in fv:
                            continue
                        field, val = fv.split("=", 1)
                        conditional_services.append({
                            "service": svc,
                            "condition_field": field.strip(),
                            "condition_value": val.strip(),
                            "trigger_service": trigger,
                        })

                cleaned.append({
                    "step": step,
                    "name": name,
                    "mode": mode,
                    "services": services,
                    "handler": handler,
                    "users": users,
                    "conditional_services": conditional_services,
                })

            def to_int(x):
                try:
                    return int(x or 0)
                except Exception:
                    return 0

            cleaned.sort(key=lambda x: to_int(x.get("step")))

            self.context.pipeline_stages = json.dumps(cleaned, ensure_ascii=False)
            return req.response.redirect(self.context.absolute_url() + "/@@process")

        return self.index()