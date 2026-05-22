# -*- coding:utf-8 -*-

import logging
import json
from urllib import quote

from bika.lims.utils import get_samples_filter_link, get_project_link
from bika.lims import api as lims_api
from senaite.core.browser.patient.subject_field_config import ANALYSIS_DEFAULT_FIELDS
from Products.Five import BrowserView
from Products.CMFDiffTool.utils import safe_unicode
from plone import api

try:
    from DateTime import DateTime
except Exception:
    DateTime = None

logger = logging.getLogger(__name__)


def _is_string(v):
    try:
        return isinstance(v, basestring)
    except Exception:
        return isinstance(v, str)


def _to_unicode(v):
    try:
        if isinstance(v, unicode):
            return v
        if isinstance(v, str):
            return v.decode("utf-8", "ignore")
        return unicode(v)
    except Exception:
        try:
            return unicode(str(v), "utf-8", "ignore")
        except Exception:
            return u""


VALID_AR_STATES = (
    "sample_due",
    "sample_received",
    "to_be_verified",
    "verified",
    "published",
)

INVALID_AR_STATES = (
    "cancelled",
    "retracted",
    "rejected",
)


class SubjectsView(BrowserView):
    DEFAULT_PAGESIZE = 50
    PAGESIZE_CHOICES = (25, 50, 100, 200)

    def catalog(self):
        return api.portal.get_tool("senaite_catalog_sample")

    def _call(self, v):
        try:
            return v() if callable(v) else v
        except Exception:
            return v

    def _fmt_dt(self, dt):
        """Format DateTime to YYYY-MM-DD HH:MM"""
        if not dt:
            return ""
        try:
            if DateTime is not None and isinstance(dt, DateTime):
                return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass
        try:
            return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return str(dt)

    def _brain_get(self, brain, *names):
        for n in names:
            v = getattr(brain, n, None)
            v = self._call(v)
            if v not in (None, "", [], ()):
                return v
        return ""

    def _obj_get(self, obj, *names):
        for n in names:
            try:
                fn = getattr(obj, n, None)
                v = fn() if callable(fn) else fn
                if v not in (None, "", [], ()):
                    return v
            except Exception:
                continue
        return ""

    def _ethnicity_display(self, v):
        mapping = {
            u"Chinese": u"中国",
            u"Foreigner": u"外籍",
        }
        return mapping.get(v, v) or u"—"

    def pagesize(self):
        try:
            ps = int(self.request.get("pagesize") or self.DEFAULT_PAGESIZE)
        except Exception:
            ps = self.DEFAULT_PAGESIZE
        if ps not in self.PAGESIZE_CHOICES:
            ps = self.DEFAULT_PAGESIZE
        return ps

    def b_start(self):
        try:
            return max(0, int(self.request.get("b_start") or 0))
        except Exception:
            return 0

    def search_text(self):
        return (self.request.get("SearchableText") or "").strip()

    def _norm(self, v):
        """py2: 转成 unicode + lower，用于包含匹配"""
        if v is None:
            return u""
        try:
            if isinstance(v, str):
                try:
                    v = v.decode("utf-8")
                except Exception:
                    v = v.decode("utf-8", "ignore")
            return (unicode(v)).strip().lower()
        except Exception:
            try:
                return (unicode(str(v))).strip().lower()
            except Exception:
                return u""

    def get_rows(self, limit=2000):
        cat = self.catalog()
        brains = cat(
            portal="AnalysisRequest",
            review_state=list(VALID_AR_STATES),
            sort_on="created",
            sort_order="reverse")

        st = self.search_text()
        needle = self._norm(st)
        matched_subjects = set() if needle else None

        seen = set()
        all_rows = []

        for b in brains:
            st = self._brain_get(b, "review_state", "ReviewState")
            if st in INVALID_AR_STATES:
                continue

            suid = self._brain_get(b, "getSubjectUID", "SubjectUID")
            if not suid:
                continue

            if needle:
                sid = self._brain_get(b, "getId", "id") or ""
                scode = self._brain_get(b, "getSampleCode", "SampleCode") or ""
                hay = u" ".join([self._norm(suid), self._norm(sid), self._norm(scode)])
                if needle in hay:
                    matched_subjects.add(suid)

            # 去重：只保存每个检测者的最新样本行（按照created倒序取第一个）
            if suid in seen:
                continue
            seen.add(suid)

            sample_id = self._brain_get(b, "getId", "id") or ""
            latest_created = self._fmt_dt(self._brain_get(b, "created", "Created"))

            cancer = self._brain_get(b, "getCancerType", "CancerType")
            project = self._brain_get(b, "getProjectName", "ProjectName", "Project")
            sample_type = self._brain_get(b, "getSampleTypeTitle", "SampleTypeTitle")
            creator_id = self._brain_get(b, "Creator", "creator")
            creator_full = lims_api.get_user_fullname(creator_id) or creator_id
            center = self._brain_get(b, "getClientTitle", "ClientTitle", "Client")
            eth = self._brain_get(b, "getEthnicity", "Ethnicity")
            sample_code = self._brain_get(b, "getSampleCode", "SampleCode")

            need_obj = not (cancer and project and sample_type and creator_id and center and eth and sample_code)
            if need_obj:
                try:
                    ar = b.getObject()
                except Exception:
                    ar = None

                if ar is not None:
                    if not cancer:
                        cancer = self._obj_get(ar, "getCancerType", "CancerType")
                    if not project:
                        project = self._obj_get(ar, "getProjectName", "ProjectName", "getProject", "Project")
                    if not sample_type:
                        sample_type = self._obj_get(ar, "getSampleTypeTitle", "getSampleType", "SampleType")
                    if not creator_id:
                        creator_id = self._obj_get(ar, "Creator", "creator")
                        creator_full = lims_api.get_user_fullname(creator_id) or creator_id
                    if not center:
                        try:
                            c = self._obj_get(ar, "getClient", "Client")
                            center = c.Title() if c else ""
                        except Exception:
                            center = ""
                    if not eth:
                        eth = self._obj_get(ar, "getEthnicity", "Ethnicity")
                    if not sample_code:
                        sample_code = self._obj_get(ar, "getSampleCode", "SampleCode")

            all_rows.append({
                "subject_uid": suid,
                "latest_sample_url": b.getURL(),
                "latest_sample_id": sample_id,
                "latest_created": latest_created,
                "cancer_type": cancer,
                "project": project,
                "sample_type": sample_type,
                "creator": creator_full,
                "creator_id": creator_id,
                "center_name": center,
                "ethnicity": self._ethnicity_display(eth),
                "sample_code": sample_code,
            })

            row = all_rows[-1]
            project_param = u""

            if row.get("cancer_type"):
                row["cancer_type"] = get_samples_filter_link(
                    text=row["cancer_type"],
                    context=self.context,
                    filter_kind="cancer",
                    filter_value=row["cancer_type"],
                    filter_label=row["cancer_type"],
                    project=project_param,
                )

            if row.get("project"):
                row["project"] = get_project_link(
                    self.context,
                    row["project"],
                    title=row["project"],
                    review_state="all",
                    csrf=False,
                )

            if row.get("creator"):
                row["creator"] = get_samples_filter_link(
                    text=row["creator"],
                    context=self.context,
                    filter_kind="creator",
                    filter_value=row.get("creator_id") or creator_id,
                    filter_label=row["creator"],
                    project=project_param,
                )

            center_name = row.get("center_name") or u""
            client_url = u""
            if ar is None:
                try:
                    ar = b.getObject()
                except Exception:
                    ar = None

            if ar is not None:
                try:
                    client = getattr(ar, "getClient", lambda: None)()
                    if client is not None:
                        client_url = safe_unicode(client.absolute_url()).strip()
                        if not center_name:
                            center_name = safe_unicode(client.Title()).strip()
                            row["center_name"] = center_name
                except Exception:
                    client_url = u""

            if center_name and client_url:
                row["center_name"] = u"<a href='%s'>%s</a>" % (client_url, safe_unicode(center_name))

            try:
                ar_obj = ar if (need_obj and ar is not None) else None
            except Exception:
                ar_obj = None

            if row.get("sample_type") and ar_obj is not None:
                try:
                    st_uid = self._obj_get(ar_obj, "getSampleTypeUID")
                except Exception:
                    st_uid = ""
                if st_uid:
                    row["sample_type"] = get_samples_filter_link(
                        text=row["sample_type"],
                        context=self.context,
                        filter_kind="sample_type",
                        filter_value=st_uid,
                        filter_label=row["sample_type"],
                        project=project_param,
                    )

            if len(all_rows) >= limit:
                break

        if needle:
            filtered = [r for r in all_rows if r["subject_uid"] in matched_subjects]
        else:
            filtered = all_rows

        self._total_subjects = len(filtered)
        self.total = self._total_subjects

        bs = self.b_start()
        ps = self.pagesize()
        return filtered[bs: bs + ps]

    def total_subjects(self):
        return int(getattr(self, "_total_subjects", 0) or 0)

    def subject_url(self, subject_uid):
        return "%s/@@subject?uid=%s" % (self.context.absolute_url(), subject_uid)

    def get_pagesize(self):
        try:
            return int(self.request.get("pagesize", 50) or 50)
        except Exception:
            return 50

    def get_b_start(self):
        try:
            return int(self.request.get("b_start", 0) or 0)
        except Exception:
            return 0

    def get_total(self):
        return int(getattr(self, "_total_subjects", 0) or 0)

    def _page_url(self, b_start):
        req = self.request
        pagesize = self.get_pagesize()
        q = req.get("SearchableText", "") or ""
        base = self.context.absolute_url() + "/subjects"

        params = [
            "b_start=%s" % int(b_start),
            "pagesize=%s" % int(pagesize),
        ]
        if q:
            params.append("SearchableText=%s" % quote(q.encode("utf-8")))
        return base + "?" + "&".join(params)

    def pager(self):
        total = self.get_total()
        pagesize = self.get_pagesize()
        b_start = self.get_b_start()

        if pagesize <= 0:
            pagesize = 50
        if total <= pagesize:
            return {}

        cur_page = (b_start // pagesize) + 1
        last_page = ((total - 1) // pagesize) + 1
        prev_start = max(0, b_start - pagesize)
        next_start = min((last_page - 1) * pagesize, b_start + pagesize)

        return {
            "total": total,
            "pagesize": pagesize,
            "b_start": b_start,
            "cur_page": cur_page,
            "last_page": last_page,
            "prev_url": self._page_url(prev_start) if cur_page > 1 else "",
            "next_url": self._page_url(next_start) if cur_page < last_page else "",
        }


class SubjectView(BrowserView):
    def sample_catalog(self):
        return api.portal.get_tool("senaite_catalog_sample")

    def analysis_catalog(self):
        for cid in ("senaite_catalog_analysis", "portal_catalog"):
            try:
                return api.portal.get_tool(cid)
            except Exception:
                continue
        return api.portal.get_tool("portal_catalog")

    def subject_uid(self):
        return _to_unicode((self.request.get("uid") or "").strip())

    def _fmt_dt(self, dt):
        if not dt:
            return ""
        try:
            if DateTime is not None and isinstance(dt, DateTime):
                return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass
        try:
            return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return str(dt)

    def _call_or_value(self, x):
        try:
            return x() if callable(x) else x
        except Exception:
            return x

    def _is_empty_value(self, v):
        if v is None:
            return True
        if v is False:
            return False
        if _is_string(v):
            return (v.strip() == "")
        if isinstance(v, (list, tuple, set)):
            return len(v) == 0
        if isinstance(v, dict):
            return len(v.keys()) == 0
        return False

    def sample_brains(self):
        uid = self.subject_uid()
        if not uid:
            return []

        cat = self.sample_catalog()

        # 第一步：用 SubjectUID 查 probe，精确过滤防止索引分词误匹配
        probe = cat(
            portal_type="AnalysisRequest",
            getSubjectUID=uid,
            review_state=list(VALID_AR_STATES),
            sort_on="created",
            sort_order="reverse",
        )
        probe = [b for b in probe if (getattr(b, "getSubjectUID", "") or "").strip() == uid]

        if not probe:
            return []

        # 第二步：尝试读取 HospitalPatientID
        hpid = ""
        ar0 = None

        # 先从 brain Metadata 读（快）
        try:
            hpid = getattr(probe[0], "getHospitalPatientID", "") or ""
            if callable(hpid):
                hpid = hpid()
            hpid = (hpid or "").strip()
        except Exception:
            hpid = ""

        # brain 读不到则 fallback 到对象
        if not hpid:
            try:
                ar0 = probe[0].getObject()
                fn = getattr(ar0, "getHospitalPatientID", None)
                hpid = fn() if callable(fn) else (fn or "")
                hpid = (hpid or "").strip()
            except Exception:
                hpid = ""

        # 第二步补充：验证 hpid 有效性
        # 排除 hpid 实际上是 ClientID 的情况（字段误读/方法重写导致）
        if hpid:
            entry_client_id = ""
            try:
                if ar0 is None:
                    ar0 = probe[0].getObject()
                fn_cid = getattr(ar0, "getClientID", None)
                entry_client_id = fn_cid() if callable(fn_cid) else (fn_cid or "")
                entry_client_id = (entry_client_id or "").strip()
            except Exception:
                pass

            if entry_client_id and hpid == entry_client_id:
                hpid = ""

        # 第三步：根据 hpid 决定走哪条路
        if hpid:
            brains = cat(
                portal_type="AnalysisRequest",
                getHospitalPatientID=hpid,
                review_state=list(VALID_AR_STATES),
                sort_on="created",
                sort_order="reverse",
            )

            # 只保留同一中心的样本，跨中心相同 hpid 视为脏数据
            entry_client_uid = ""
            try:
                if ar0 is None:
                    ar0 = probe[0].getObject()
                client0 = ar0.getClient()
                if client0:
                    entry_client_uid = client0.UID()
            except Exception:
                pass

            if entry_client_uid:
                filtered_brains = []
                for b in brains:
                    try:
                        c = b.getObject().getClient()
                        if c and c.UID() == entry_client_uid:
                            filtered_brains.append(b)
                    except Exception:
                        filtered_brains.append(b)
                brains = filtered_brains

            # 若过滤后仍有多个不同 suid，说明同一中心内 hpid 碰撞，不合并
            suids_found = set(
                (getattr(b, "getSubjectUID", "") or "").strip() for b in brains
            )
            suids_found.discard("")
            if len(suids_found) > 1:
                return probe

            return brains
        else:
            return probe

    def patient_info(self):
        brains = self.sample_brains()
        if not brains:
            return {}

        ar = brains[0].getObject()

        def _safe_call(obj, name, default=""):
            try:
                fn = getattr(obj, name, None)
                if callable(fn):
                    return fn() or default
            except Exception:
                pass
            return default

        # 收集所有关联的 SubjectUID（去重保序）
        seen_suids = []
        seen_set = set()
        for b in brains:
            try:
                suid = getattr(b, "getSubjectUID", "") or ""
                if callable(suid):
                    suid = suid()
                suid = (suid or "").strip()
            except Exception:
                suid = ""
            if suid and suid not in seen_set:
                seen_set.add(suid)
                seen_suids.append(suid)

        eth = _safe_call(ar, "getEthnicity", default=u"")
        eth_display = {u"Chinese": u"中国", u"Foreigner": u"外籍"}.get(eth, eth) or u"—"

        return {
            "SubjectUID": u", ".join(seen_suids) if seen_suids else _safe_call(ar, "getSubjectUID"),
            "Diagnosis": _safe_call(ar, "getDiagnosis"),
            "EthnicGroup": _safe_call(ar, "getEthnicGroup"),
            "CancerType": _safe_call(ar, "getCancerType"),
            "Client": _safe_call(ar, "getClient").Title() if _safe_call(ar, "getClient") else u"—",
            "Ethnicity": eth_display,
        }

    def analyses_for_ar(self, ar):
        cat = self.analysis_catalog()
        try:
            ar_path = "/".join(ar.getPhysicalPath())
        except Exception:
            return []

        VALID_STATES = (
            "to_be_verified",
            "verified",
        )

        INVALID_STATES = (
            "cancelled",
            "retracted",
            "rejected",
        )

        tab = (self.request.get("lab_analyses_review_state")
               or self.request.get("review_state_tab")
               or "default")

        query = {
            "path": {"query": ar_path, "depth": 2},
            "portal_type": "Analysis",
            "sort_on": "created",
            "sort_order": "reverse",
        }

        if tab == "invalid":
            query["review_state"] = list(INVALID_STATES)
        elif tab == "all":
            pass
        else:
            query["review_state"] = list(VALID_STATES)

        brains = cat(**query) or []

        analyses = []
        for b in brains:
            try:
                analyses.append(b.getObject())
            except Exception:
                continue
        return analyses

    def _get_interim_definitions(self, analysis):
        try:
            svc = analysis.getAnalysisService()
        except Exception:
            svc = None
        if not svc:
            return []

        try:
            defs = svc.getInterimFields() or []
        except Exception:
            defs = []

        out = []
        for d in defs:
            if not isinstance(d, dict):
                continue
            kw = d.get("keyword")
            if not kw:
                continue
            out.append(d)
        return out

    def _get_interim_storage(self, analysis):
        try:
            fn = getattr(analysis, "getInterimFields", None)
            if callable(fn):
                raw = fn()
                if raw is not None:
                    return raw
        except Exception:
            pass
        try:
            getField = getattr(analysis, "getField", None)
            if callable(getField):
                field = getField("InterimFields")
                if field:
                    raw = field.get(analysis)
                    if raw is not None:
                        return raw
        except Exception:
            pass
        try:
            raw = getattr(analysis, "InterimFields", None)
            if raw is not None:
                return raw
        except Exception:
            pass

        return None

    def _interim_value_for_keyword(self, analysis, kw, raw_storage):
        if isinstance(raw_storage, dict):
            v = raw_storage.get(kw)
            if isinstance(v, dict):
                return v.get("value"), (v.get("formatted_value") or v.get("value"))
            return v, v

        if isinstance(raw_storage, (list, tuple)):
            for item in raw_storage:
                if not isinstance(item, dict):
                    continue
                if item.get("keyword") == kw:
                    return item.get("value"), (item.get("formatted_value") or item.get("value"))

        try:
            fn = getattr(analysis, "getInterimFieldValue", None)
            if callable(fn):
                v = fn(kw)
                return v, v
        except Exception:
            pass

        return None, None

    def _normalize_result_type(self, interim_def):
        rtype = (interim_def.get("result_type") or interim_def.get("type") or "").strip().lower()
        if rtype in ("multivalue:tiered", "tiered_multivalue", "multivalue:tiered"):
            return "tiered_multivalue"
        return rtype or "default"

    def _safe_json_loads(self, s):
        if s is None:
            return None
        try:
            if isinstance(s, unicode):
                s = s.encode("utf-8")
        except Exception:
            pass
        try:
            return json.loads(s)
        except Exception:
            return None

    def _pretty_json(self, obj):
        try:
            return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False)
        except Exception:
            try:
                return _to_unicode(obj)
            except Exception:
                return u""

    def _display_value(self, value, unit=u""):
        if self._is_empty_value(value):
            return u"—"

        value = self._call_or_value(value)
        if isinstance(value, (list, tuple, set)):
            parts = []
            for x in value:
                parts.append(_to_unicode(self._call_or_value(x)))
            text = u", ".join(parts)
        elif isinstance(value, dict):
            text = self._pretty_json(value)
        else:
            text = _to_unicode(value)

        text = text.strip()
        if unit and text and text != u"—":
            return u"%s %s" % (text, _to_unicode(unit))
        return text or u"—"

    def _handle_tiered_multivalue(self, row):
        raw = row.get("value")
        if raw is None:
            raw = row.get("formatted_value")

        parsed = None
        if isinstance(raw, (dict, list, tuple)):
            parsed = raw
        elif _is_string(raw):
            s = raw.strip()
            if (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]")):
                parsed = self._safe_json_loads(s)

        if parsed is None:
            row["render"] = "text"
            row["display"] = self._display_value(raw, row.get("unit"))
            row["json_pretty"] = ""
            row["summary"] = ""
            return

        row["render"] = "json"
        row["json_pretty"] = self._pretty_json(parsed)

        summary = u""
        if isinstance(parsed, dict):
            status = parsed.get("status")
            values = parsed.get("values")
            if status or values:
                summary = u""
                if status:
                    summary += u"status=%s" % _to_unicode(status)
                if values is not None:
                    if summary:
                        summary += u", "
                    summary += u"values=%s" % _to_unicode(values)
        row["summary"] = summary

    def extract_interim_results(self, analysis, include_empty=True):

        defs = self._get_interim_definitions(analysis)
        raw = self._get_interim_storage(analysis)

        results = []
        for d in defs:
            kw = d.get("keyword")
            title = d.get("title") or kw
            unit = d.get("unit") or ""
            rtype = self._normalize_result_type(d)

            value, fvalue = self._interim_value_for_keyword(analysis, kw, raw)

            value = self._call_or_value(value)
            fvalue = self._call_or_value(fvalue)

            has_value = (not self._is_empty_value(fvalue)) or (not self._is_empty_value(value))

            if (not include_empty) and (not has_value):
                continue

            row = {
                "title": title,
                "keyword": kw,
                "unit": unit,
                "result_type": rtype,
                "value": value,
                "formatted_value": fvalue,
                "has_value": has_value,
                "render": "default",
                "display": self._display_value(fvalue if fvalue is not None else value, unit),
                "json_pretty": "",
                "summary": "",
            }

            if rtype == "tiered_multivalue":
                self._handle_tiered_multivalue(row)

            results.append(row)

        return results

    def _analysis_to_card(self, an):

        uid = ""
        try:
            uid = an.UID()
        except Exception:
            uid = ""

        try:
            title = an.Title()
        except Exception:
            title = getattr(an, "title", "") or ""

        try:
            url = an.absolute_url()
        except Exception:
            url = ""

        try:
            state = api.content.get_state(obj=an)
        except Exception:
            state = ""

        analyst = ""
        try:
            analyst = self._analyst_display(an.getAnalyst() or "")
        except Exception:
            pass

        created = ""
        try:
            c = getattr(an, "created", None)
            c = self._call_or_value(c)
            created = self._fmt_dt(c)
        except Exception:
            created = ""

        service_title = ""
        try:
            svc = an.getAnalysisService()
            if svc:
                try:
                    service_title = svc.Title()
                except Exception:
                    service_title = getattr(svc, "title", "") or ""
        except Exception:
            pass

        try:
            interim_rows = self.extract_interim_results(an, include_empty=True)
        except Exception:
            interim_rows = []

        default_keywords = None
        if ANALYSIS_DEFAULT_FIELDS and service_title:
            if service_title in ANALYSIS_DEFAULT_FIELDS:
                default_keywords = set(ANALYSIS_DEFAULT_FIELDS[service_title])

        has_any_value = False
        for r in interim_rows:
            if r.get("has_value"):
                has_any_value = True

            if default_keywords is None:
                r["default_visible"] = True
            else:
                r["default_visible"] = (r.get("keyword") in default_keywords)

        return {
            "uid": uid,
            "title": title,
            "url": url,
            "state": state,
            "analyst": analyst,
            "created": created,
            "service_title": service_title,
            "interim": interim_rows,
            "show_results_default": bool(has_any_value),
        }

    def samples_with_analyses(self):
        items = []
        for b in self.sample_brains():
            try:
                st = getattr(b, "review_state", "") or ""
            except Exception:
                st = ""
            if st in INVALID_AR_STATES:
                continue

            ar = b.getObject()
            analyses = self.analyses_for_ar(ar)
            cards = [self._analysis_to_card(an) for an in analyses]

            sid = ""
            try:
                fn = getattr(b, "getId", None)
                sid = fn() if callable(fn) else (fn or "")
            except Exception:
                sid = ""

            items.append({
                "id": sid,
                "url": b.getURL(),
                "created": self._fmt_dt(getattr(b, "created", "")),
                "analyses": cards,
            })

        return items

    def _analyst_display(self, userid):
        if not userid:
            return u""
        try:
            m = api.user.get(username=userid)
            if not m:
                return _to_unicode(userid)

            fullname = m.getProperty("fullname", "") or m.getProperty("FullName", "")
            if fullname:
                return _to_unicode(fullname)
            title = m.getProperty("title", "")
            if title:
                return _to_unicode(title)
            return _to_unicode(m.getId() or userid)
        except Exception:
            return _to_unicode(userid)