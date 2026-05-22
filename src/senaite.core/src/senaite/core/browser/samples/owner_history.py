# -*- coding: utf-8 -*-

from __future__ import absolute_import, unicode_literals

import json
import logging
import pytz
from collections import OrderedDict
from datetime import datetime

from DateTime import DateTime as ZDT
from Products.Five.browser import BrowserView
from Products.Five.browser.pagetemplatefile import ViewPageTemplateFile
from Products.CMFCore.utils import getToolByName

try:
    from senaite.core import api
except Exception:
    api = None

try:
    from Products.CMFPlone.utils import safe_unicode
except Exception:
    safe_unicode = None

try:
    from plone import api as plone_api
except Exception:
    plone_api = None

LOG = logging.getLogger("owner_history")


def _u(v):
    try:
        if isinstance(v, unicode):
            return v
    except Exception:
        pass
    if v is None:
        return u""
    try:
        if safe_unicode:
            return safe_unicode(v)
    except Exception:
        pass
    try:
        return unicode(v, 'utf-8')
    except Exception:
        try:
            return unicode(str(v), 'utf-8', 'ignore')
        except Exception:
            return u""


def _log_safe(v):
    """将任意对象转换为适合日志的 unicode 文本。"""
    try:
        if isinstance(v, (list, tuple, dict)):
            return _u(json.dumps(v, ensure_ascii=False))
        return _u(v)
    except Exception:
        try:
            return _u(repr(v))
        except Exception:
            return u""


def LOGI(fmt, *args):
    """unicode 安全的 info 日志"""
    try:
        if args:
            LOG.info(_u(fmt), *tuple(_log_safe(a) for a in args))
        else:
            LOG.info(_u(fmt))
    except Exception:
        # 极端情况下直接拼接
        try:
            LOG.info(_u(fmt) + u" " + u" ".join(_log_safe(a) for a in args))
        except Exception:
            pass


def LOGW(fmt, *args):
    """unicode 安全的 warning 日志"""
    try:
        if args:
            LOG.warning(_u(fmt), *tuple(_log_safe(a) for a in args))
        else:
            LOG.warning(_u(fmt))
    except Exception:
        try:
            LOG.warning(_u(fmt) + u" " + u" ".join(_log_safe(a) for a in args))
        except Exception:
            pass


def _as_dt(v):
    """把 workflow 的 time 转成 datetime/DateTime"""
    try:
        if isinstance(v, ZDT):
            return v
    except Exception:
        pass
    if isinstance(v, datetime):
        return v
    try:
        s = v if isinstance(v, basestring) else unicode(v)
        s = s.replace("T", " ").split(".")[0]
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def _get_obj_by_uid(context, uid):
    """兼容获取对象：优先 senaite.api；失败则用 portal_catalog。"""
    if not uid:
        return None
    # 1) senaite api
    if api:
        getter = getattr(api, "get_object_by_uid", None) or getattr(api, "get_object", None)
        if callable(getter):
            try:
                obj = getter(uid)
                if obj:
                    return obj
            except Exception:
                pass
    # 2) catalog
    catalog = getToolByName(context, "portal_catalog")
    brains = catalog(UID=uid)
    return brains[0].getObject() if brains else None


def _to_u(v):
    try:
        if isinstance(v, unicode):
            return v
        if isinstance(v, str):
            return v.decode('utf-8', 'ignore')
        return unicode(v) if v is not None else u""
    except Exception:
        return u""


def _iter_interims(a):
    get_if = getattr(a, "getInterimFields", None)
    if not callable(get_if):
        return []
    try:
        data = get_if() or []
        return data if isinstance(data, list) else []
    except Exception as e:
        LOGW("[owner_history] getInterimFields() failed for %s: %s", a, e)
        return []


def _results_from_interims(a):
    """只从 Analysis.getInterimFields() 抓取所有结果，保持出现顺序。
       统一返回 list of (title, value)；空值用空串。
    """
    rows = []
    for f in _iter_interims(a):
        # 兼容不同键名/大小写
        title = _to_u(f.get("title") or f.get("Title") or f.get("keyword") or u"")
        value = _to_u(f.get("value") or f.get("Value") or u"")
        rows.append((title, value))
    # LOGI("[owner_history][interims] %s: %d items, first=%s",
    #      getattr(a, "Title", lambda: repr(a))(), len(rows), rows[:1])
    return rows


def _site_tz():
    tzname = None
    try:
        if plone_api:
            tzname = plone_api.portal.get_current_timezone()
    except Exception:
        tzname = None
    if not tzname:
        tzname = "Asia/Shanghai"
    tz = pytz.timezone(tzname)
    # LOGI("[tz] resolved site timezone: %s", tz.zone)
    return tz


def _fmt_local(dt):
    if not dt:
        return u""
    tz = _site_tz()

    # ---- Zope DateTime：不要用 strftime！改走 UTC 时间戳 -> Python datetime ----
    if isinstance(dt, ZDT):
        try:
            # Zope DateTime.timeTime() 返回 UTC 秒数（float）
            ts = float(dt.timeTime())
            py_utc = datetime.utcfromtimestamp(ts).replace(tzinfo=pytz.UTC)
            out = py_utc.astimezone(tz).strftime("%Y-%m-%d %H:%M")
            # LOGI("[tz] _fmt_local OUT(ZDT->py) in=%r ts=%s -> %s", dt, ts, out)
            return out
        except Exception as e:
            LOGW("[tz] _fmt_local ZDT convert failed: %s; fallback to toZone/strftime", e)
            return dt.toZone(tz.zone).strftime("%Y-%m-%d %H:%M")

    # ---- Python datetime：naive 当作 UTC，再转站点时区 ----
    if dt.tzinfo is None:
        # LOGI("[tz] _fmt_local ASSUME input is UTC: %r", dt)
        dt = pytz.UTC.localize(dt)
    out = dt.astimezone(tz).strftime("%Y-%m-%d %H:%M")
    # LOGI("[tz] _fmt_local OUT(datetime) in=%r -> %s", dt, out)
    return out


class OwnerHistoryFillsByUserView(BrowserView):
    """按用户分组展示历史 + 阶段视图（sample_due / sample_received） + 已分配的Analysis（条件 + 结果）"""

    template = ViewPageTemplateFile("templates/owner_history.pt")

    # 写死：每个阶段需要展示的字段（按显示顺序）
    # (schema 字段名, 页面显示名-中文，且在同一阶段内必须唯一)
    FIELDS_BY_PHASE = {
        # 阶段①：sample_due（样本填写）
        "sample_due": [
            ("SubjectUID", u"受试者唯一编码"),
            ("Client", u"中心"),
            ("Contact", u"联系人"),
            ("DateSampled", u"采样日期"),
            ("SampleType", u"样本类型"),
            ("Diagnosis", u"疾病诊断"),
            ("EthnicGroup", u"民族"),
            ("CancerType", u"癌种"),
            ("ProjectName", u"项目名称"),
            ("SampleCode", u"样本编号"),
            ("Priority", u"优先级"),
            ("ThermometerStatus", u"温度计是否有效"),
            ("ThermometerCode", u"温度计编号"),
            ("BoxPreCooled", u"运箱是否预冷"),
            ("SampleIsolated", u"是否与冰袋隔离"),
            ("BoxPadded", u"是否填充防震"),
            ("TemperatureRecorded", u"是否记录2–8℃"),
            ("TransportMethod", u"运输方式"),
            ("Shipper", u"承运人"),
            ("ShippingTime", u"发货时间"),
            ("StorageConditions", u"存储条件"),
            ("Remarks", u"备注"),
        ],

        # 阶段②：sample_received（样本接收）
        "sample_received": [
            ("over24h", u"采后是否超过24h"),
            ("labelMatch", u"标签是否与交接信息一致"),
            ("pkgIntact", u"运输外包装是否完好"),
            ("tempValid", u"运输温度是否2–8℃且有记录"),
            ("pkgStatus", u"样本包装是否正常"),
            ("pkgStatusNote", u"包装异常说明"),
            ("DateReceived", u"接收时间"),
            ("StorageConditions", u"存储条件"),
            ("Remarks", u"备注"),
        ],
    }

    COMMON_RESULT_FIELD_NAMES = [
        "Result", "ResultValue", "Result_value", "Value", "ResultText", "Detection",
        "Conclusion", "Interpretation", "Judgement", "QC", "Ct", "OD", "CopyNumber",
        "Alleles", "Genotype", "Typing", "ReportableResult"
    ]

    def _display_user(self, userid):
        uid = userid or ""
        disp = uid
        # 1 senaite.core.api 优先
        try:
            if api and uid:
                uobj = api.get_user(uid)
                fn = api.get_fullname(uobj) if uobj else ""
                if fn:
                    return fn, uid
        except Exception:
            pass
        # 2 退到 Plone membership 属性
        try:
            mtool = getToolByName(self.context, "portal_membership", None)
            if mtool:
                m = mtool.getMemberById(uid)
                fn = m.getProperty("fullname") if m else ""
                if fn:
                    return fn, uid
        except Exception:
            pass
        # 3 仍然拿不到就用 userid
        return disp, uid

    def _skip_result_meta(self, title, keyword=None):
        """过滤掉‘预定义结果/排序/结果类型’等元配置项"""
        t = (title or u"").strip().lower()
        k = (keyword or u"").strip().lower()

        skip_titles = {
            u"预定义结果",  # 中文标题
            u"sorting criteria",
            u"result type",
        }
        # 英文/中文标题的 lower 后匹配
        if t in {s.lower() for s in skip_titles}:
            return True

        skip_keys = {
            "predefined_results", "default_results", "preset_results",
            "result_options", "sorting_criteria", "result_type",
            "resulttype", "sorting", "resulttext_sorting"
        }

        if k in skip_keys:
            return True

        return False

    def u(self, v):
        """模板里排序/显示前做 unicode 安全转换。"""
        try:
            return _u(v)
        except Exception:
            return u""

    def render_cond_value(self, field):
        """把条件字段的值友好地渲染成字符串."""
        # field 形如 {'title': u'备注', 'value': u'...', 'choices': ..., 'attachment': ..., ...}
        if not isinstance(field, dict):
            return self.u(field)

        val = field.get('value', u'')
        # 列表/元组：用顿号连接
        if isinstance(val, (list, tuple, set)):
            return u"、".join([self.u(x) for x in val if x not in (None, u"", "", [], {}, set())])
        # 优先显示 title 或 value
        if isinstance(val, dict):
            return self.u(val.get('title') or val.get('value') or u"")
        # 其他标量
        return self.u(val)

    def __call__(self):
        # 取 uid，但找不到就回退到当前对象，避免 None
        uid = self.request.form.get("uid")
        ctx = _get_obj_by_uid(self.context, uid) if uid else None
        context = ctx or self.context

        # 日志：入口
        try:
            url = getattr(context, "absolute_url", lambda: "")()
        except Exception:
            url = ""

        include_descendants = True  # 包括子对象
        entries = self._collect_history(context, include_descendants)

        rows = [self._normalize_row(e) for e in entries]
        # 仅展示若干种类，避免日志过长
        first_types = list({(r.get('obj_type'), r.get('action'), r.get('review_state')) for r in rows})[:6]

        # 排序：按时间
        rows.sort(key=lambda r: (r.get("time") or datetime.min))

        # 过滤：关键节点（保留审核）
        rows = self._filter_rows(rows)

        # 分组（按操作人）
        from collections import OrderedDict
        buckets = OrderedDict()
        for idx, r in enumerate(rows):
            actor_key = r.get("actor") or ""
            r["_seq"] = idx  # 稳定次序备用
            buckets.setdefault(actor_key, []).append(r)

        segments = []
        for actor_key, items in buckets.items():
            items.sort(key=lambda x: (x.get("time") or datetime.min, x.get("_seq", 0)))
            segments.append({
                "actor": actor_key,
                "actor_display": items[0].get("actor_display") or actor_key if items else actor_key,
                "events": items,
            })
        self.segments = segments
        self.context_obj = context
        self.phases = self._build_phases(rows)
        self.assigned_analyses = self._build_assigned_analyses()
        self.user_handoffs = self._build_user_handoffs(self.segments)
        self.handoff_chain = self.get_handoff_chain()
        self.user_roles = self._build_user_roles(self.user_handoffs)

        return self.template()

    # ------------- 阶段视图 -------------

    def _build_phases(self, rows):
        phases = []

        ar = self._get_context_ar(self.context_obj)

        def _first_event(states_or_actions):
            """在 rows 里取最早的匹配事件，用于抬头显示时间/操作人/对象"""
            for r in rows:
                st = (r.get("review_state") or "").lower()
                ac = (r.get("action") or "").lower()
                if st in states_or_actions or ac in states_or_actions:
                    return r
            return None

        # 阶段①：sample_due
        ev_due = _first_event(set(["sample_due", "sample_registered", "no_sampling_workflow"]))
        if ev_due:
            data = {}
            for fname, label in self.FIELDS_BY_PHASE["sample_due"]:
                val = self._value_of(ar, fname)
                if not self._is_empty(val):
                    data[label] = val
            phases.append({
                "key": "sample_due",
                "title": u"阶段① 样本填写（sample_due）",
                "event": {
                    "time_str": ev_due.get("time_str"),
                    "actor": ev_due.get("actor"),
                    "actor_display": ev_due.get("actor_display") or ev_due.get("actor"),
                    "obj_title_display": ev_due.get("obj_title_display") or ev_due.get("obj_title"),
                },
                "data": data,
            })

        # 阶段②：sample_received
        ev_recv = _first_event(set(["sample_received"]))
        if ev_recv:
            data = {}
            for fname, label in self.FIELDS_BY_PHASE["sample_received"]:
                val = self._value_of(ar, fname)
                if not self._is_empty(val):
                    data[label] = val
            phases.append({
                "key": "sample_received",
                "title": u"阶段② 样本接收（sample_received）",
                "event": {
                    "time_str": ev_recv.get("time_str"),
                    "actor": ev_recv.get("actor"),
                    "actor_display": ev_recv.get("actor_display") or ev_recv.get("actor"),
                    "obj_title_display": ev_recv.get("obj_title_display") or ev_recv.get("obj_title"),
                },
                "data": data,
            })

        self._phase_count = sum(len(p.get("data", {})) for p in phases)
        return phases

    # ---------- 读字段工具（写死字段用） ----------

    def _pretty(self, v):
        try:
            from DateTime.DateTime import DateTime
            if isinstance(v, DateTime):
                # return v.asdatetime().strftime("%Y-%m-%d %H:%M")
                return _fmt_local(v)

        except Exception:
            pass
        if isinstance(v, datetime):
            return v.strftime("%Y-%m-%d %H:%M")
        try:
            if hasattr(v, "Title"):
                return v.Title()
        except Exception:
            pass
        if isinstance(v, (list, tuple, set)):
            items = [unicode(self._pretty(x)) for x in v if not self._is_empty(x)]
            return u", ".join(items)
        if isinstance(v, bool):
            return u"是" if v else u"否"
        return v

    def _is_empty(self, v):
        if v is None:
            return True
        if isinstance(v, (list, tuple, dict, set)):
            return len(v) == 0
        try:
            s = _u(v).strip()
            return s == u""
        except Exception:
            return False

    def _value_of(self, ar, field_name):
        """读取 AR 的指定字段：getter → schema.accessor → field.get() → field.getRaw() + 反解 UID → getattr"""
        if ar is None:
            return None

        # 1) 常见 getter：get<FieldName>
        getter = getattr(ar, "get{}".format(field_name), None)
        if callable(getter):
            try:
                val = getter()
                val = self._pretty(val)
                if not self._is_empty(val):
                    return val
            except Exception:
                pass

        # 2) Schema accessor / get / getRaw
        try:
            schema = getattr(ar, "Schema", None)
            schema = schema() if callable(schema) else schema
            if schema:
                fld = schema.get(field_name)
                if fld:
                    # accessor
                    acc = getattr(fld, "getAccessor", lambda: None)()
                    if callable(acc):
                        try:
                            val = acc()
                            val = self._pretty(val)
                            if not self._is_empty(val):
                                return val
                        except Exception:
                            pass
                    # get()
                    try:
                        val = fld.get(ar)
                        val = self._pretty(val)
                        if not self._is_empty(val):
                            return val
                    except Exception:
                        pass
                    # getRaw() + 反解 UID
                    try:
                        raw = getattr(fld, "getRaw", None)
                        raw = raw(ar) if callable(raw) else None
                        if not self._is_empty(raw):
                            def _uid_to_title(uid):
                                try:
                                    obj = _get_obj_by_uid(self.context, uid)
                                    if obj:
                                        t = getattr(obj, "Title", lambda: "")()
                                        return t or uid
                                except Exception:
                                    pass
                                return uid

                            if isinstance(raw, (list, tuple)):
                                val = [_uid_to_title(x) for x in raw]
                            else:
                                val = _uid_to_title(raw)
                            val = self._pretty(val)
                            if not self._is_empty(val):
                                return val
                    except Exception:
                        pass
        except Exception:
            pass

        try:
            val = getattr(ar, field_name, None)
            val = self._pretty(val)
            if not self._is_empty(val):
                return val
        except Exception:
            pass

        return None

    def _get_context_ar(self, context):
        """从当前上下文找到 AR 对象"""
        if getattr(context, "portal_type", "") == "AnalysisRequest":
            return context
        try:
            if api:
                ar = getattr(api, "get_analysisrequest", None)
                if callable(ar):
                    res = ar(context)
                    if res and getattr(res, "portal_type", "") == "AnalysisRequest":
                        return res
        except Exception:
            pass
        # 向上找
        try:
            obj = context
            while obj:
                if getattr(obj, "portal_type", "") == "AnalysisRequest":
                    return obj
                obj = getattr(obj, "aq_parent", None)
        except Exception:
            pass
        return None

    # ------------- 过滤（含审核保留） -------------
    def _filter_rows(self, rows):
        """
        规则（关键节点 + 审核不丢失）：
          - AR：sample_registered / sample_due（无 action），no_sampling_workflow，receive，
                以及审核/发布（action: verify/publish 或 state: to_be_verified/verified/published）
          - Analysis：assign、submit 以及审核/发布
          - assign 事件尝试拼上“→ 指派给 XXX”
        """
        KEEP_AR_STATES = set(["sample_registered", "sample_due"])  # 无 action
        KEEP_AR_ACTIONS = set(["no_sampling_workflow", "receive"])

        REVIEW_ACTIONS = set(["verify", "publish"])
        REVIEW_STATES = set(["to_be_verified", "verified", "published"])

        KEEP_ANALYSIS_ACTIONS = set(["assign", "submit"]) | REVIEW_ACTIONS

        filtered = []
        for r in rows:
            obj_type = (r.get("obj_type") or "").strip()
            action = (r.get("action") or "").strip().lower()
            state = (r.get("review_state") or "").strip().lower()

            # 审核节点优先放行
            if action in REVIEW_ACTIONS or state in REVIEW_STATES:
                filtered.append(r)
                continue

            # AnalysisRequest（样本）
            if obj_type == "AnalysisRequest":
                if not action and state in KEEP_AR_STATES:
                    filtered.append(r)
                    continue
                if action in KEEP_AR_ACTIONS:
                    filtered.append(r)
                    continue
                continue

            # Analysis（分析项目）
            if obj_type == "Analysis":
                if action in KEEP_ANALYSIS_ACTIONS:
                    if action == "assign":
                        assignee = self._guess_assignee(r.get("details", {}))
                        if assignee:
                            note = u" → 指派给 %s" % assignee
                            orig = r.get("comments") or ""
                            r["comments"] = (orig + note) if orig else note
                    filtered.append(r)
                continue

            # 其它类型默认丢弃
        return filtered

    def _guess_assignee(self, details):
        """从 changes/payload 里尽量获取到 assign 的接收人（用户名）"""
        if not details:
            return ""

        changes = details.get("changes")
        payload = details.get("payload")
        if not changes and payload:
            changes = payload.get("changes") or payload

        def flatten_list_to_dict(lst):
            d = {}
            for it in lst or []:
                if isinstance(it, dict) and "field" in it:
                    d[it["field"]] = {"old": it.get("old"), "new": it.get("new")}
            return d

        if isinstance(changes, list):
            changes = flatten_list_to_dict(changes)

        candidates = [
            "Analyst", "AssignedTo", "assignee", "assigned_to",
            "Responsible", "owner", "Owner", "AnalystID"
        ]

        # dict 结构
        if isinstance(changes, dict):
            for k in candidates:
                v = changes.get(k)
                cand = v.get("new") or v.get("to") if isinstance(v, dict) else None
                if cand:
                    try:
                        if api:
                            uobj = api.get_user(cand)
                            fullname = api.get_fullname(uobj) if uobj else ""
                            return fullname or cand
                    except Exception:
                        return cand
        # payload 平级键
        if isinstance(payload, dict):
            for k in candidates:
                if k in payload and payload.get(k):
                    cand = payload.get(k)
                    try:
                        if api:
                            uobj = api.get_user(cand)
                            fullname = api.get_fullname(uobj) if uobj else ""
                            return fullname or cand
                    except Exception:
                        return cand
        return ""

    # ------------- 历史收集 -------------
    def _collect_history(self, root, include_descendants=True):
        """收集 root 及其“子对象 + 关联对象(Analyses等)”的 workflow 历史"""
        if root is None:
            return []

        objs = [root]

        # 1) 物理后代（路径下）
        if include_descendants:
            try:
                catalog = getToolByName(root, "portal_catalog")
                path = "/".join(root.getPhysicalPath())
                brains = catalog(path={"query": path})
                for b in brains:
                    try:
                        o = b.getObject()
                        if o:
                            objs.append(o)
                    except Exception:
                        continue
            except Exception:
                pass

        # 2) 逻辑关联（尤其是 Analysis）
        related = []

        # senaite api
        try:
            if api:
                getter = getattr(api, "get_analyses", None)
                if callable(getter):
                    try:
                        related.extend(list(getter(root)))
                    except TypeError:
                        try:
                            related.extend(list(getter(context=root)))
                        except Exception:
                            pass
        except Exception:
            pass

        # 方法兼容
        for name in ("getAnalyses", "getAnalysesFull", "get_analysis", "analyses"):
            try:
                meth = getattr(root, name, None)
                if callable(meth):
                    res = meth() if meth.__code__.co_argcount <= 1 else meth(getall=True)
                    if res:
                        for r in res:
                            try:
                                if hasattr(r, "getObject"):
                                    related.append(r.getObject())
                                elif getattr(r, "aq_base", None) is not None or hasattr(r, "workflow_history"):
                                    related.append(r)
                            except Exception:
                                continue
            except Exception:
                pass

        # 去重
        uniq = {}
        for o in objs + related:
            try:
                uniq[id(o)] = o
            except Exception:
                continue
        objects = list(uniq.values())

        # 汇总历史
        all_entries = []
        for obj in objects:
            all_entries.extend(self._extract_obj_history(obj))
        return all_entries

    def _extract_obj_history(self, obj):
        """从单个对象抽取 workflow_history 记录"""
        entries = []
        wfh = getattr(obj, "workflow_history", {}) or {}
        for wf_id, records in wfh.items():
            for rec in records or []:
                entries.append({
                    "time": rec.get("time"),
                    "actor": rec.get("actor") or "",
                    "action": rec.get("action") or "",
                    "review_state": rec.get("review_state") or "",
                    "comments": rec.get("comments") or "",
                    "obj": obj,
                    "obj_type": getattr(obj, "portal_type", ""),
                    "obj_title": getattr(obj, "Title", lambda: "")(),
                    "changes": rec.get("changes") or {},
                    "payload": rec.get("payload") or {},
                })
        return entries

    def _normalize_row(self, e):
        """统一字段，准备给模板使用"""
        dt = _as_dt(e.get("time"))
        time_local = _fmt_local(dt) if dt else ""
        # LOGI("[tz] row-time raw=%r type=%s -> local=%s action=%s state=%s",
        #      e.get("time"), type(dt).__name__ if dt else None, time_local,
        #      e.get("action"), e.get("review_state"))

        actor = e.get("actor") or ""

        actor_display = actor
        try:
            if api and actor:
                user = api.get_user(actor)
                fn = api.get_fullname(user) if user else ""
                if fn:
                    actor_display = fn
        except Exception:
            pass
        if actor_display == actor and actor:
            try:
                mtool = getToolByName(self.context, "portal_membership", None)
                if mtool:
                    m = mtool.getMemberById(actor)
                    fn = m.getProperty("fullname") if m else ""
                    if fn:
                        actor_display = fn
            except Exception:
                pass

        obj = e.get("obj")
        obj_type_display = e.get("obj_type") or ""
        obj_title_display = e.get("obj_title") or ""
        try:
            if api and obj is not None:
                get_pt = getattr(api, "get_portal_type_title", None)
                get_ti = getattr(api, "get_title", None)
                if callable(get_pt):
                    obj_type_display = get_pt(obj) or obj_type_display
                if callable(get_ti):
                    obj_title_display = get_ti(obj) or obj_title_display
        except Exception:
            pass

        details = {
            "changes": e.get("changes") or {},
            "payload": e.get("payload") or {},
        }

        return {
            "time": dt,
            "time_str": time_local,
            "actor": actor,
            "actor_display": actor_display,
            "action": e.get("action") or "",
            "review_state": e.get("review_state") or "",
            "obj_type": e.get("obj_type") or "",
            "obj_type_display": obj_type_display,
            "obj_title": e.get("obj_title") or "",
            "obj_title_display": obj_title_display,
            "comments": e.get("comments") or "",
            "details": details,
        }

    # ------------- 工具：JSON/changes 展平 -------------

    def pretty_json(self, data):
        try:
            return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
        except Exception:
            return unicode(data)

    def flatten_changes(self, changes):
        """把 workflow 里千奇百怪的 changes 统一成 [{field, old, new}] 列表。"""
        rows = []

        # dict 形式
        if isinstance(changes, dict):
            for fname, val in changes.items():
                old = new = None
                if isinstance(val, dict):
                    old = val.get("old", val.get("from"))
                    new = val.get("new", val.get("to"))
                elif isinstance(val, (list, tuple)) and len(val) >= 2:
                    old, new = val[0], val[1]
                else:
                    new = val
                rows.append({"field": fname, "old": old, "new": new})
            return rows

        # list / tuple 形式
        if isinstance(changes, (list, tuple)):
            if changes and isinstance(changes[0], dict) and "field" in changes[0]:
                for it in changes:
                    rows.append({"field": it.get("field"), "old": it.get("old"), "new": it.get("new")})
                return rows
            try:
                d = dict(changes)
                if "field" in d and ("new" in d or "old" in d):
                    rows.append({"field": d.get("field"), "old": d.get("old"), "new": d.get("new")})
                    return rows
            except Exception:
                pass
            for pair in changes:
                try:
                    k, v = pair
                    rows.append({"field": k, "old": None, "new": v})
                except Exception:
                    continue
        return rows

    def _get_event_object(self, row):
        ob = None
        try:
            ob = row.get("obj")
        except Exception:
            pass
        if ob is not None:
            return ob
        try:
            payload = (row.get("details") or {}).get("payload") or {}
            uid = payload.get("obj_uid") or payload.get("object_uid") or payload.get("uid")
            if uid:
                ob = _get_obj_by_uid(self.context, uid)
                return ob
        except Exception:
            pass
        return None

    # ================= Assigned Analyses（条件 + 结果） =================
    def _get_current_user(self):
        """返回 (userid, fullname)"""
        userid = ""
        fullname = ""
        try:
            if api:
                uobj = api.get_current_user()
                userid = api.get_userid(uobj) or ""
                fullname = api.get_fullname(uobj) or userid
        except Exception:
            pass
        if not userid:
            mtool = getToolByName(self.context, "portal_membership", None)
            if mtool:
                m = mtool.getAuthenticatedMember()
                userid = m.getId() if m else ""
                fullname = m.getProperty("fullname") or userid if m else userid
        return userid, fullname

    def _is_privileged(self, userid):
        """管理员/审核等角色不过滤"""
        try:
            if api:
                roles = set(api.get_roles(userid) or [])
                if roles & set(["Manager", "LabManager", "LabClerk", "Reviewer", "Verifier", "Publisher", "Owner"]):
                    return True
        except Exception:
            pass
        # 退化：site-wide检查
        try:
            mtool = getToolByName(self.context, "portal_membership")
            user = mtool.getMemberById(userid)
            if user:
                roles = set(user.getRoles())
                if roles & set(["Manager", "Owner", "Site Administrator"]):
                    return True
        except Exception:
            pass
        return False

    def _iter_analyses_of_context(self):
        ar = self._get_context_ar(self.context_obj)
        if not ar:
            return []

        seen = {}

        try:
            if api:
                getter = getattr(api, "get_analyses", None)
                if callable(getter):
                    try:
                        for a in getter(ar) or []:
                            seen[id(a)] = a
                    except TypeError:
                        for a in getter(context=ar) or []:
                            seen[id(a)] = a
        except Exception:
            pass

        for name in ("getAnalyses", "getAnalysesFull", "analyses", "get_analysis"):
            try:
                meth = getattr(ar, name, None)
                if callable(meth):
                    res = meth() if getattr(meth, "__code__", None) and meth.__code__.co_argcount <= 1 else meth(
                        getall=True)
                    for it in res or []:
                        obj = it.getObject() if hasattr(it, "getObject") else it
                        if obj is not None and getattr(obj, "portal_type", "") == "Analysis":
                            seen[id(obj)] = obj
            except Exception:
                pass

        try:
            catalog = getToolByName(self.context, "portal_catalog")
            try:
                path = "/".join(ar.getPhysicalPath())
                brains = catalog(portal_type="Analysis",
                                 path={"query": path, "depth": 5})
                for b in brains:
                    try:
                        o = b.getObject()
                        if o and getattr(o, "portal_type", "") == "Analysis":
                            seen[id(o)] = o
                    except Exception:
                        continue
            except Exception:
                pass

            # 3.2 常见反向引用索引
            uid = getattr(ar, "UID", lambda: None)() or ""
            if uid:
                for idx in ("getRequestUID", "getAnalysisRequestUID", "getAnalysisRequestsUIDs",
                            "ARUID", "AnalysisRequestUID", "RequestUID"):
                    try:
                        brains = catalog(portal_type="Analysis", **{idx: uid})
                        for b in brains:
                            try:
                                o = b.getObject()
                                if o and getattr(o, "portal_type", "") == "Analysis":
                                    seen[id(o)] = o
                            except Exception:
                                continue
                    except Exception:
                        continue
        except Exception:
            pass

        objs = list(seen.values())

        return objs

    def _service_title(self, a):
        try:
            if api and hasattr(api, "get_title"):
                svc = getattr(a, "getService", lambda: None)()
                if svc:
                    return api.get_title(svc) or getattr(svc, "Title", lambda: "")()
        except Exception:
            pass
        try:
            svc = getattr(a, "getService", lambda: None)()
            if svc:
                return getattr(svc, "Title", lambda: "")() or getattr(svc, "title", "")
        except Exception:
            pass
        # 退化：Analysis 自身标题
        try:
            return getattr(a, "Title", lambda: "")() or getattr(a, "title", "")
        except Exception:
            return ""

    def _analyst_of(self, a):
        """返回 (userid, display)。优先对象字段；拿不到再从 assign 的 workflow 记录里找。"""
        uid = ""
        disp = ""

        for name in ("getAnalyst", "Analyst", "getAssignedTo", "AssignedTo",
                     "Responsible", "getResponsible", "AnalystID", "getAnalystID"):
            try:
                attr = getattr(a, name, None)
                v = attr() if callable(attr) else attr
                if v:
                    uid = v if isinstance(v, basestring) else getattr(v, "getUserName", lambda: "")() or ""
                    break
            except Exception:
                continue

        if not uid:
            try:
                wfh = getattr(a, "workflow_history", {}) or {}
                for records in wfh.values():
                    for rec in (records or []):
                        if (rec.get("action") or "").lower() != "assign":
                            continue
                        payload = rec.get("payload") or {}
                        changes = rec.get("changes") or payload.get("changes") or payload
                        # 兼容各种键名
                        for k in ("Analyst", "AssignedTo", "assignee", "assigned_to",
                                  "Responsible", "owner", "Owner", "AnalystID"):
                            v = None
                            if isinstance(changes, dict) and k in changes:
                                ch = changes[k]
                                v = ch.get("new") if isinstance(ch, dict) else ch
                            elif isinstance(payload, dict) and k in payload:
                                v = payload.get(k)
                            if v:
                                uid = v
                                break
                        if uid:
                            break
                    if uid:
                        break
            except Exception:
                pass

        try:
            if api and uid:
                uobj = api.get_user(uid)
                disp = api.get_fullname(uobj) or ""
        except Exception:
            pass
        if not disp and uid:
            try:
                mtool = getToolByName(self.context, "portal_membership", None)
                if mtool:
                    m = mtool.getMemberById(uid)
                    disp = m.getProperty("fullname") if m else ""
            except Exception:
                pass
        return uid or "", (disp or uid or "")

    def _extract_conditions(self, a):
        from collections import OrderedDict
        conds = OrderedDict()

        def _merge(title, value, source_tag):
            t = _u(title or u"").strip()
            v = self._pretty(value)
            if not t or self._is_empty(v):
                return
            key = t
            k = 2
            while key in conds:
                key = u"%s (%d)" % (t, k)
                k += 1
            conds[key] = v

        got_gc = False
        for name in ("getConditions", "Conditions", "getCondition", "Condition"):
            fn = getattr(a, name, None)
            try:
                val = fn() if callable(fn) else None
            except Exception as e:
                LOGW("[owner_history][conditions] %s() error: %s", name, e)
                val = None

            if not val:
                continue

            got_gc = True

            if isinstance(val, dict):
                for k, v in val.items():
                    _merge(k, v, "getConditions:dict")
                break

            if isinstance(val, (list, tuple)):
                for it in val:
                    try:
                        if isinstance(it, dict):
                            # 常见键名兼容
                            t = it.get("title") or it.get("field") or it.get("name") or u""
                            v = it.get("value", "")
                            if self._is_empty(v):
                                v = it.get("new", "")
                            _merge(t, v, "getConditions:list-dict")
                        else:
                            t = getattr(it, "Title", lambda: "")() or getattr(it, "title", "") or u"条件"
                            if hasattr(it, "getValue"):
                                v = it.getValue()
                            else:
                                v = getattr(it, "value", it)
                            _merge(t, v, "getConditions:list-obj")
                    except Exception as e:
                        LOGW("[owner_history][conditions] getConditions item error: %s", e)
                break

            _merge(u"条件", val, "getConditions:scalar")
            break

        try:
            schema = a.Schema() if callable(getattr(a, "Schema", None)) else getattr(a, "Schema", None)
        except Exception:
            schema = None

        if schema:
            for fname in schema.keys():
                if "condition" not in (fname or "").lower():
                    continue
                try:
                    fld = schema.get(fname)
                    label = getattr(getattr(fld, "widget", None), "label", None) or fname
                    acc = getattr(fld, "getAccessor", lambda: None)()
                    try:
                        val = acc() if callable(acc) else fld.get(a)
                    except Exception:
                        try:
                            val = fld.get(a)
                        except Exception:
                            val = getattr(a, fname, None)
                    _merge(label, val, "schema")
                except Exception as e:
                    LOGW("[owner_history][conditions] schema %s error: %s", fname, e)
        else:
            LOGI("[owner_history][conditions] no schema on analysis")

        try:
            wfh = getattr(a, "workflow_history", {}) or {}
            for records in wfh.values():
                for rec in records or []:
                    payload = rec.get("payload") or {}
                    for k in ("conditions", "condition", "Condition", "Conditions"):
                        v = payload.get(k)
                        if not v:
                            continue
                        found_payload = True
                        if isinstance(v, dict):
                            for kk, vv in v.items():
                                _merge(kk, vv, "payload:dict")
                        elif isinstance(v, (list, tuple)):
                            for it in v:
                                if isinstance(it, dict):
                                    t = it.get("title") or it.get("field") or it.get("name") or u""
                                    val = it.get("value") or it.get("new") or it.get("val")
                                    _merge(t, val, "payload:list-dict")
                                else:
                                    _merge(u"条件", it, "payload:list-obj")
                        else:
                            _merge(u"条件", v, "payload:scalar")
        except Exception as e:
            LOGW("[owner_history][conditions] scan payload error: %s", e)

        if not conds:
            return {}

        # 打印前若干条，避免日志过大
        preview = [(k, conds[k]) for k in list(conds.keys())[:6]]
        return dict(conds)

    def _pretty_val(self, v):
        try:
            # None / 空字符串 / 仅空白
            if v is None:
                return u""
            if isinstance(v, basestring):
                return v.decode("utf-8") if isinstance(v, str) else v
            # 列表/字典：序列化为紧凑 JSON
            if isinstance(v, (list, tuple, dict)):
                try:
                    try:
                        if isinstance(v, dict) and ("images" in v) and ("values" in v):
                            LOGI("[owner_history][_pretty_val][IMG-PAYLOAD] keys=%s images=%s values_len=%s",
                                 sorted(list(v.keys())),
                                 len(v.get("images") or []),
                                 len(v.get("values") or []))
                    except Exception:
                        pass
                    return json.dumps(v, ensure_ascii=False)
                except Exception:
                    return unicode(v)
            return unicode(v)
        except Exception:
            try:
                return unicode(v)
            except Exception:
                return u""

    def _is_empty_val(self, v):
        # None / 空容器
        if v is None:
            return True
        if isinstance(v, (list, tuple, dict)) and len(v) == 0:
            return True

        # bytes → unicode
        if isinstance(v, str):
            try:
                v = v.decode('utf-8')
            except Exception:
                v = unicode(v, 'utf-8', 'ignore')

        if isinstance(v, unicode):
            s = v.strip().lower().replace(u'\u00a0', u'')
            EMPTY = {
                u'', u'-', u'—', u'–',
                u'na', u'n/a', u'none', u'null',
                u'未设置', u'未设定', u'无', u'暂未填写', u'不适用',
                u'[]', u'{}'
            }
            return s in EMPTY
        return False

    def _extract_results(self, a):
        """提取一个 Analysis 的结果（schema结果 + 常见字段 + interims）。
        额外支持：若 interim value 是 JSON 字符串且包含 images/values，则解析并存入 __payload__。
        """
        results = OrderedDict()
        schema = None

        # 1) schema: schemata 名含 result/结果
        try:
            schema = a.Schema() if callable(getattr(a, "Schema", None)) else getattr(a, "Schema", None)
            if schema:
                for fname in schema.keys():
                    fld = schema.get(fname)
                    try:
                        schem = getattr(fld, "schemata", "") or getattr(fld, "getSchemata", lambda: "")()
                    except Exception:
                        schem = ""
                    sname = (schem or "").lower()
                    if ("result" in sname) or (u"结果" in sname):
                        label = getattr(getattr(fld, "widget", None), "label", None) or fname
                        try:
                            acc = getattr(fld, "getAccessor", lambda: None)()
                            val = acc() if callable(acc) else fld.get(a)
                        except Exception:
                            try:
                                val = fld.get(a)
                            except Exception:
                                val = getattr(a, fname, None)

                        val = self._pretty_val(val)

                        # 最小黑名单
                        if fname in ('ResultOptions', 'ResultOptionsSorting'):
                            continue
                        if self._skip_result_meta(_u(label), fname):
                            continue

                        if not self._is_empty_val(val):
                            label_u = label if isinstance(label, unicode) else unicode(label)
                            results.setdefault(label_u, val)
        except Exception:
            pass

        # 2) 常见结果字段名（Result/Conclusion/...）
        for fname in getattr(self, "COMMON_RESULT_FIELD_NAMES", []):
            try:
                val = getattr(a, fname, None)
                if val is None and schema:
                    fld = schema.get(fname)
                    if fld:
                        try:
                            acc = getattr(fld, "getAccessor", lambda: None)()
                            val = acc() if callable(acc) else fld.get(a)
                        except Exception:
                            try:
                                val = fld.get(a)
                            except Exception:
                                val = getattr(a, fname, None)

                val = self._pretty_val(val)

                if fname in ('ResultOptions', 'ResultOptionsSorting'):
                    continue
                if self._skip_result_meta(_u(fname), fname):
                    continue

                if not self._is_empty_val(val):
                    label = fname
                    if schema:
                        fld = schema.get(fname) if schema else None
                        w = getattr(fld, "widget", None) if fld else None
                        label = getattr(w, "label", None) or fname
                    label_u = label if isinstance(label, unicode) else unicode(label)
                    if label_u not in results:
                        results[label_u] = val
            except Exception:
                continue

        # 3) 合并 getInterimFields()
        try:
            interims = getattr(a, "getInterimFields", lambda: [])()

            for f in interims or []:
                try:
                    title_raw = f.get("title") or f.get("keyword") or u"未命名字段"
                    title_u = _u(title_raw)

                    raw_v = f.get("value")
                    value_u = _u(self._pretty_val(raw_v))

                    if self._is_empty_val(value_u) or self._skip_result_meta(title_u, f.get("keyword")):
                        continue

                    key = title_u
                    if key in results:
                        k = 2
                        while u"%s (%d)" % (title_u, k) in results:
                            k += 1
                        key = u"%s (%d)" % (title_u, k)

                    # 原逻辑：字符串结果照样放进 results（不破坏现有页面）
                    results[key] = value_u
                    try:
                        s = _u(raw_v).strip()
                        if s.startswith(u"{") and s.endswith(u"}"):
                            data = json.loads(s)
                            if isinstance(data, dict) and ("images" in data) and ("values" in data):
                                payload = {
                                    "status": _u(data.get("status") or u""),
                                    "values": data.get("values") or [],
                                    "images": data.get("images") or [],
                                }
                                results.setdefault(u"__payload__", {})[key] = payload
                    except Exception:
                        pass

                except Exception as item_e:
                    LOGW("[owner_history][interims_item] skip one field: %s", item_e)
                    continue
        except Exception as e:
            LOGW("[owner_history][interims_all] failed: %s", e)

        if not results:
            return None

        return dict(results)

    def _build_assigned_analyses(self):
        # —— 小工具：同一“分析项目”的归一 key —— #
        def _service_key_of(a):
            # 优先服务对象 UID
            for getter in ("getAnalysisService", "getService"):
                g = getattr(a, getter, None)
                if callable(g):
                    try:
                        svc = g()
                        if svc and hasattr(svc, "UID"):
                            return u"svc:" + svc.UID()
                    except Exception:
                        pass
            # 其它可用标识
            for getter in ("getServiceUID", "getKeyword"):
                g = getattr(a, getter, None)
                try:
                    v = g() if callable(g) else g
                except Exception:
                    v = None
                if v:
                    return u"svc:" + (v if isinstance(v, unicode) else unicode(v))
            # 退化：用服务标题
            title = self._service_title(a) or getattr(a, "Title", lambda: u"")()
            return u"svc:" + (title if isinstance(title, unicode) else unicode(title))

        def _analysis_ts(a):
            for name in (
                    "getResultCaptureDate",
                    "getDateAnalysisPublished",
                    "getDateAnalyzed",
                    "getDateReceived",
                    "modified",
                    "created",
            ):
                v = getattr(a, name, None)
                try:
                    v = v() if callable(v) else v
                except Exception:
                    v = None
                if v:
                    return v
            return 0

        # 先收集（带时间戳），只收集“有有效结果”的记录
        items = []
        for a in self._iter_analyses_of_context():
            if getattr(a, "portal_type", "") != "Analysis":
                continue

            service = self._service_title(a)
            analyst_id, analyst_disp = self._analyst_of(a)

            conditions = self._extract_conditions(a)
            results = self._extract_results(a)

            if not results:
                continue

            results_payload = {}
            try:
                if isinstance(results, dict):
                    results_payload = results.pop(u"__payload__", {}) or {}
            except Exception:
                results_payload = {}

            items.append({
                "ts": _analysis_ts(a),
                "key": _service_key_of(a),  # 用于“同一项目”去重
                "service": service,
                "analyst_id": analyst_id,
                "analyst": analyst_disp,
                "conditions": conditions or {},
                "results": results or {},
                "results_payload": results_payload or {},  # ★给 PT 渲染图2用
            })

        items.sort(key=lambda x: x["ts"], reverse=True)

        seen = set()
        uniq = []
        for it in items:
            k = it.get("key")
            if not k or k in seen:
                continue
            seen.add(k)
            uniq.append(it)

        return uniq

    def _flatten_events_from_segments(self, segments):
        flat = []
        for seg in (segments or []):
            actor_id = seg.get("actor") or ""  # 用户名
            actor_disp = seg.get("actor_display") or actor_id
            for ev in seg.get("events", []):
                flat.append({
                    "time_str": ev.get("time_str") or "",
                    "action": ev.get("action") or "",
                    "state": ev.get("review_state") or "",
                    "obj_type": ev.get("obj_type_display") or ev.get("obj_type") or "",
                    "service": ev.get("obj_title_display") or ev.get("obj_title") or "",
                    "actor_id": actor_id,
                    "actor_display": actor_disp,
                    "comments": ev.get("comments") or "",
                })
        flat = [e for e in flat if e["obj_type"] == "Analysis" and e["action"] in ("assign", "submit", "verify")]
        flat.sort(key=lambda x: x["time_str"])
        return flat

    def _build_user_handoffs(self, segments):
        """
        构建“用户→用户”的接力链（已合并阶段与实验流）：
          1【阶段①】sample_due 填写人 → sample_received 接收人（说明取 sample_due）
          2【阶段②】sample_received 接收人 → 第一个实验操作者（说明取 sample_received）
          3 保持原有的实验流事件（assign→submit→verify），说明取对应左侧 src 事件
        若阶段事件缺失，则自动跳过对应边，整体逻辑不变。
        """

        def append_edge(edges, frm_id, frm_disp, to_id, to_disp,
                        when=u"", service=u"", step_label=u"", state=u"", comments=u"",
                        phase_id=u"", phase_title=u""):
            # 同一对用户的连续动作合并到 details
            if edges and edges[-1]["frm_id"] == frm_id and edges[-1]["to_id"] == to_id:
                edges[-1].setdefault("details", []).append({
                    "when": when, "service": service, "step_label": step_label,
                    "state": state, "comments": comments,
                    "phase_id": phase_id, "phase_title": phase_title,
                })
            else:
                edges.append({
                    "frm_id": frm_id, "frm": frm_disp, "frm_fullname": frm_disp,
                    "to_id": to_id, "to": to_disp, "to_fullname": to_disp,
                    "when": when, "service": service, "step_label": step_label, "state": state,
                    "phase_id": phase_id, "phase_title": phase_title,
                    "details": [{
                        "when": when, "service": service, "step_label": step_label,
                        "state": state, "comments": comments,
                        "phase_id": phase_id, "phase_title": phase_title,
                    }],
                })

        # 获取姓名和用户ID
        def get_fullname_and_id(uid):
            disp, _uid = self._display_user(uid or "")
            return (_uid or ""), (disp or (_uid or ""))

        # ===== 取“阶段事件”：优先 self.phases，没有就从 segments 里推断 =====
        sd_ev = None  # sample_due 的事件 dict
        sr_ev = None  # sample_received 的事件 dict

        # 1先从self.phases找
        try:
            phs = getattr(self, "phases", None) or getattr(self, "_phases", None) or []
            if phs and len(phs) > 0 and isinstance(phs[0], dict):
                sd_ev = (phs[0].get("event") or None)
            if phs and len(phs) > 1 and isinstance(phs[1], dict):
                sr_ev = (phs[1].get("event") or None)
        except Exception:
            pass

        # 2若还没有，则从 segments 中找（按 review_state 或 phase_id）
        if not (sd_ev and sr_ev):
            all_rows = []
            for seg in (segments or []):
                for ev in (seg.get("events") or []):
                    all_rows.append(ev)

            def find_phase(rows, names):
                names = {(n or "").lower() for n in (names or [])}
                for r in rows:
                    rs = (r.get("review_state") or r.get("state") or r.get("phase_id") or "").lower()
                    ph = (r.get("phase_title") or "").lower()
                    if rs in names or ph in names:
                        return r
                return None

            sd_ev = sd_ev or find_phase(all_rows, ["sample_due", "sample_registered", "no_sampling_workflow"])
            sr_ev = sr_ev or find_phase(all_rows, ["sample_received"])

        # 标准化需要用到的字段
        def ev_to_edge_bits(ev, default_phase_id=u"", default_phase_title=u""):
            if not ev:
                return dict(actor_id=u"", actor_display=u"", when=u"", phase_id=u"", phase_title=u"")
            actor_id = ev.get("actor") or ev.get("actor_id") or u""
            # 若没填 actor_display，这里也统一走 _display_user
            _id, _disp = actor_id, None
            try:
                _id, _disp = actor_id, self._display_user(actor_id)[0]
            except Exception:
                pass
            return dict(
                actor_id=actor_id,
                actor_display=_disp or ev.get("actor_display") or actor_id or u"",
                when=ev.get("time_str") or ev.get("time") or ev.get("date") or u"",
                phase_id=(ev.get("phase_id") or default_phase_id or u""),
                phase_title=(ev.get("phase_title") or default_phase_title or u""),
                service=ev.get("service_title") or ev.get("service") or u"",
                step=ev.get("step") or ev.get("action_label") or u"",
                state=ev.get("state") or ev.get("review_state") or u"",
                comments=ev.get("comments") or u"",
            )

        sd_bits = ev_to_edge_bits(sd_ev, default_phase_id=u"sample_due",
                                  default_phase_title=u"样本填写（sample_due）") if sd_ev else None
        sr_bits = ev_to_edge_bits(sr_ev, default_phase_id=u"sample_received",
                                  default_phase_title=u"样本接收（sample_received）") if sr_ev else None

        # ===== 原有“实验事件”拍平 & 找到起点=====
        events = self._flatten_events_from_segments(
            segments)  # 需返回按时间顺序的事件：action/actor_id/time_str/service/state/comments
        current_owner_id = None
        current_owner_disp = None

        # 起点优先 assign 的受让人，没有就用第一个 submit 的提交人
        for e in events:
            if (e.get("action") or "") == "assign":
                current_owner_id = e.get("actor_id") or ""
                current_owner_disp = self._display_user(current_owner_id)[0] if current_owner_id else ""
                break

        if current_owner_id is None:
            for e in events:
                if (e.get("action") or "") == "submit":
                    current_owner_id = e.get("actor_id") or ""
                    current_owner_disp = self._display_user(current_owner_id)[0] if current_owner_id else ""
                    break

        edges = []
        verify_done = False

        # 1) 阶段①：sample_due 填写人 → sample_received 接收人（说明=sample_due）
        if sd_bits and sr_bits and sd_bits["actor_id"] and sr_bits["actor_id"]:
            frm_id, frm_disp = get_fullname_and_id(sd_bits["actor_id"])
            to_id, to_disp = get_fullname_and_id(sr_bits["actor_id"])
            append_edge(edges, frm_id, frm_disp, to_id, to_disp,
                        when=sd_bits["when"], service=u"", step_label=u"", state=u"", comments=u"",
                        phase_id=sd_bits["phase_id"], phase_title=sd_bits["phase_title"])

        # 阶段②：sample_received 接收人 → 第一个实验操作者（说明=sample_received）
        if sr_bits and sr_bits["actor_id"]:
            frm_id, frm_disp = get_fullname_and_id(sr_bits["actor_id"])
            if current_owner_id:
                to_id, to_disp = current_owner_id, current_owner_disp
            else:
                if sd_bits and sd_bits["actor_id"]:
                    to_id, to_disp = get_fullname_and_id(sd_bits["actor_id"])
                else:
                    to_id, to_disp = frm_id, frm_disp
            append_edge(edges, frm_id, frm_disp, to_id, to_disp,
                        when=sr_bits["when"], service=u"", step_label=u"", state=u"", comments=u"",
                        phase_id=sr_bits["phase_id"], phase_title=sr_bits["phase_title"])

        # 若没有任何实验事件，阶段两条边足够了
        if not current_owner_id:
            return edges

        # ===== 继续原有“实验流”事件（说明取 src 段 = 左侧当前持有者）=====
        for e in events:
            if verify_done:
                break
            action = (e.get("action") or "")
            if action == "assign":
                # assign 只变更持有者，不画边（与原逻辑一致）
                continue

            if action == "submit":
                submitter_id = e.get("actor_id") or ""
                if submitter_id and submitter_id != current_owner_id:
                    submitter_disp = self._display_user(submitter_id)[0]
                    # 说明来自“左侧 src”：即 current_owner（assign→submit）
                    append_edge(edges,
                                current_owner_id, current_owner_disp,
                                submitter_id, submitter_disp,
                                when=e.get("time_str") or u"",
                                service=e.get("service") or e.get("service_title") or u"",
                                step_label=u"assign → submit",
                                state=e.get("state") or e.get("review_state") or u"",
                                comments=e.get("comments") or u"",
                                phase_id=u"", phase_title=u"")
                    current_owner_id, current_owner_disp = submitter_id, submitter_disp

            elif action == "verify":
                verifier_id = e.get("actor_id") or ""
                if verifier_id and verifier_id != current_owner_id:
                    verifier_disp = self._display_user(verifier_id)[0]
                    # 说明来自“左侧 src”：即 current_owner（submit→verify）
                    append_edge(edges,
                                current_owner_id, current_owner_disp,
                                verifier_id, verifier_disp,
                                when=e.get("time_str") or u"",
                                service=e.get("service") or e.get("service_title") or u"",
                                step_label=u"submit → verify",
                                state=e.get("state") or e.get("review_state") or u"",
                                comments=e.get("comments") or u"",
                                phase_id=u"", phase_title=u"")
                    current_owner_id, current_owner_disp = verifier_id, verifier_disp
                verify_done = True

        return edges

    def _build_user_roles(self, user_handoffs):
        """根据用户接力链生成 {user: 序号(从1开始)}"""
        order = []
        if not user_handoffs:
            return {}
        # 起点
        first = user_handoffs[0].get("frm")
        if first:
            order.append(first)
        # 依次把 to 追加（去重，保持顺序）
        for ed in user_handoffs:
            to = ed.get("to")
            if to and to not in order:
                order.append(to)
        # 生成映射
        return {u: i + 1 for i, u in enumerate(order)}

    @property
    def analyses_by_analyst(self):
        rows = list(self.assigned_analyses or [])
        buckets = OrderedDict()

        def _name_id(row):
            name = (row.get("analyst") or "").strip()
            aid = (row.get("analyst_id") or "").strip()
            if not name and aid:
                name, _ = self._display_user(aid)
            if not name:
                name = u"(未分配)"
            return name, aid

        for r in rows:
            name, aid = _name_id(r)
            if name not in buckets:
                buckets[name] = {"analyst": name, "analyst_id": aid, "done": 0, "total": 0, "rows": []}
            buckets[name]["rows"].append(r)
            buckets[name]["done"] += int(r.get("done") or 0)
            buckets[name]["total"] += int(r.get("total") or 0)

        groups = list(buckets.values())
        groups.sort(key=lambda g: (g["analyst"] == u"(未分配)", g["analyst"]))
        return groups

    def get_handoff_chain(self):
        edges = getattr(self, "user_handoffs", None) or []
        if not edges:
            return {"nodes": [], "node_ids": [], "text": u""}

        def U(v):
            return safe_unicode(v or u"").strip()

        pairs_disp = []
        pairs_id = []
        for ed in edges:
            frm = U(ed.get("frm_fullname") or ed.get("frm"))
            to = U(ed.get("to_fullname") or ed.get("to"))
            frm_id = U(ed.get("frm_id"))
            to_id = U(ed.get("to_id"))
            if frm and to:
                pairs_disp.append((frm, to))
            if frm_id and to_id:
                pairs_id.append((frm_id, to_id))

        if not pairs_disp:
            return {"nodes": [], "node_ids": [], "text": u""}

        nodes = [pairs_disp[0][0]]
        last = pairs_disp[0][0]
        for frm, to in pairs_disp:
            if frm != last and nodes[-1] != frm:
                nodes.append(frm)
            if nodes[-1] != to:
                nodes.append(to)
            last = to

        dedup_nodes = []
        for n in nodes:
            if n and (not dedup_nodes or dedup_nodes[-1] != n):
                dedup_nodes.append(n)

        node_ids = []
        if pairs_id:
            node_ids = [pairs_id[0][0]]
            last_id = pairs_id[0][0]
            for frm_id, to_id in pairs_id:
                if frm_id != last_id and node_ids[-1] != frm_id:
                    node_ids.append(frm_id)
                if node_ids[-1] != to_id:
                    node_ids.append(to_id)
                last_id = to_id

            dedup_ids = []
            for uid in node_ids:
                if uid and (not dedup_ids or dedup_ids[-1] != uid):
                    dedup_ids.append(uid)
            node_ids = dedup_ids

        if len(node_ids) < len(dedup_nodes):
            node_ids = node_ids + [u""] * (len(dedup_nodes) - len(node_ids))
        elif len(node_ids) > len(dedup_nodes):
            node_ids = node_ids[:len(dedup_nodes)]

        return {"nodes": dedup_nodes, "node_ids": node_ids, "text": u" \u2192 ".join(dedup_nodes)}
