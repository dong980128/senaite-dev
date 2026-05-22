# -*- coding: utf-8 -*-
from __future__ import print_function

import json
import traceback
from collections import OrderedDict, namedtuple
from senaite.core.mysql import db as mysql_db
from Products.CMFCore.utils import getToolByName
from Products.CMFPlone.utils import safe_unicode

import logging

LOG = logging.getLogger("mysql.importer.hlav1")


# 禁止打印日志
class _NoopLogger(object):

    def debug(self, *a, **k):   pass

    def info(self, *a, **k):    pass

    def warning(self, *a, **k): pass

    def error(self, *a, **k):   pass

    def exception(self, *a, **k): pass

    def critical(self, *a, **k): pass


logger = _NoopLogger()


def _short(v, maxlen=200):
    try:
        s = v if isinstance(v, str) else repr(v)
    except Exception:
        s = u"<unreprable>"
    if len(s) > maxlen:
        return s[:maxlen] + u"...(trunc)"
    return s


def _peek_row(row):
    """从一行里挑关键键做个快照"""
    if not isinstance(row, dict):
        return {"__type__": type(row).__name__, "__repr__": _short(row, 120)}
    snap = {}
    for k in ("sample_id", "request_id", "subject_uid", "center_name", "client", "KshId", "patientID"):
        if k in row:
            snap[k] = _short(row.get(k))
    interims = row.get("interims") or {}
    for k in ("KshId", "HLA-A", "HLA-B", "HLA-C", "HLA-DRB1", "tumor"):
        if k in interims:
            snap["interims." + k] = _short(interims.get(k))
    return snap


# 从 senaite_catalog_analysis 读取所有 getKeyword（实验）
def get_analysis_keywords(site):
    """返回分析目录中现有的实验关键字（getKeyword）去重集合"""
    cat = getattr(site, "senaite_catalog_analysis", None) \
          or getToolByName(site, "portal_catalog")

    try:
        idx = getattr(cat, "Indexes", None)
        if idx and "getKeyword" in idx:
            values = list(idx["getKeyword"].uniqueValues())
        else:
            values = list(cat.indexes["getKeyword"].uniqueValues())
        return sorted([v for v in values if v])
    except Exception:
        try:
            brains = cat({"meta_type": "Analysis"})[:2000]
            vals = set()
            for b in brains:
                kw = getattr(b, "getKeyword", None)
                if callable(kw):
                    kw = kw()
                if kw:
                    vals.add(kw)
            return sorted(vals)
        except Exception:
            traceback.print_exc()
            return []


# 样本来源：按关键字抓 Analysis，再反推父 AR，拼装样本列表
def _safe_call(obj, name, default=None):
    try:
        if hasattr(obj, name) and callable(getattr(obj, name)):
            return getattr(obj, name)()
    except Exception:
        pass
    try:
        schema = getattr(obj, "Schema", None)
        if schema:
            field = schema().get(name) or schema().get(name.replace("get", "", 1))
            if field:
                return field.get(obj)
    except Exception:
        pass
    return default


def _resolve_primary_ar(ar_obj, site):
    # 保留：虽然本次不展示，但函数留着以后可能还用到
    try:
        if hasattr(ar_obj, "getPrimaryAnalysisRequest"):
            val = ar_obj.getPrimaryAnalysisRequest()
            if val:
                if isinstance(val, (list, tuple)):
                    return val[0] if val else None
                return val
    except Exception:
        pass
    try:
        if hasattr(ar_obj, "getRawPrimaryAnalysisRequest"):
            uid = ar_obj.getRawPrimaryAnalysisRequest()
            if isinstance(uid, (list, tuple)):
                uid = uid[0] if uid else None
            if uid:
                rc = getToolByName(site, "reference_catalog", None)
                if rc is not None:
                    return rc.lookupObject(uid)
    except Exception:
        pass
    try:
        schema = getattr(ar_obj, "Schema", None)
        if schema and "PrimaryAnalysisRequest" in schema():
            val = schema()["PrimaryAnalysisRequest"].get(ar_obj)
            if isinstance(val, (list, tuple)):
                return val[0] if val else None
            return val
    except Exception:
        pass
    return None


def samples_by_keyword(site, keyword, params):
    limit = int(params.get("limit") or 50)

    # 状态过滤：传入 'review_state' 或 'state'
    state_filter = params.get("review_state") or params.get("state") or ""
    state_set = set([s.strip() for s in state_filter.split(",") if s.strip()]) if state_filter else set()

    # 先用analysis catalog找出有哪些样本有这个实验
    cat = getattr(site, "senaite_catalog_analysis", None) \
          or getToolByName(site, "portal_catalog")

    try:
        brains = cat({"getKeyword": keyword})
    except Exception as ex:
        logger.exception("[samples_by_keyword] catalog error: %r", ex)
        return []

    seen, picked = set(), []
    for b in brains:
        try:
            rid = b.getRequestID() if hasattr(b, "getRequestID") else None
        except Exception:
            rid = None

        if not rid:
            try:
                rid = "/".join(b.getPath().split("/")[:-1])
            except Exception:
                rid = ""

        if rid in seen:
            continue

        seen.add(rid)
        picked.append(b)
        if len(picked) >= limit:
            break

    wf = getToolByName(site, "portal_workflow", None)
    rows = []

    for idx, b in enumerate(picked, 1):
        path = b.getPath()
        ar_path = "/".join(path.split("/")[:-1])

        try:
            ar_obj = site.unrestrictedTraverse(ar_path)
        except Exception:
            ar_obj = None

        request_id = ""
        sample_id = ""
        client = ""
        state = ""
        created = ""
        kw = ""
        subject_uid = ""
        interim_flat = ""
        interims_map = {}

        try:
            if hasattr(b, "getRequestID"):
                request_id = b.getRequestID()
            if hasattr(b, "getKeyword"):
                kw = b.getKeyword()
        except Exception:
            pass

        if ar_obj is not None:

            # 基本信息
            sample_id = _safe_call(ar_obj, "getSampleID", "") or getattr(ar_obj, "id", "")
            client = _safe_call(ar_obj, "getClientTitle", "") or _safe_call(ar_obj, "Title", "")

            if wf:
                try:
                    state = wf.getInfoFor(ar_obj, "review_state", "")
                except Exception:
                    state = _safe_call(b, "review_state", "")
            else:
                state = _safe_call(b, "review_state", "")

            if state_set and (state not in state_set):
                continue

            # SubjectUID
            subject_uid = _safe_call(ar_obj, "getSubjectUID", "")
            if not subject_uid:
                try:
                    if hasattr(ar_obj, "getPatient"):
                        patient = ar_obj.getPatient()
                        if patient:
                            subject_uid = getattr(patient, "UID", lambda: "")()
                except Exception:
                    pass

            analysis_obj = _resolve_analysis_obj_fallback(site, ar_path, keyword=keyword or kw)

            if analysis_obj is not None and hasattr(analysis_obj, "getInterimFields"):
                try:
                    interim = analysis_obj.getInterimFields()
                    interim_flat = json.dumps(
                        interim, ensure_ascii=False, sort_keys=True, default=str
                    )
                    interims_map = _interims_kv(analysis_obj, use_title_first=True)
                except Exception as ex:
                    logger.exception(
                        "[samples_by_keyword][%d] getInterimFields failed: %r", idx, ex
                    )

                    # 失败返回Analysis 中interim 结果
                    interim_flat = ""
                    interims_map = {}

        else:
            state = getattr(b, "review_state", "")
            if state_set and (state not in state_set):
                continue

        enthnic_group = _to_text(_safe_call(ar_obj, "getEthnicGroup", "")) if ar_obj else u""
        disease_diagnosis = _to_text(
            (_safe_call(ar_obj, "getDiagnosis", "") or
             _safe_call(ar_obj, "getClinical", ""))
        ) if ar_obj else u""
        center_name = _to_text(_safe_call(ar_obj, "getClientTitle", "")) if ar_obj else u""
        cancer_type = _to_text(_safe_call(ar_obj, "getCancerType", "")) if ar_obj else u""
        population = _to_text(_safe_call(ar_obj, "getEthnicity", "")) if ar_obj else u""
        hospital_patient_id = _to_text(_safe_call(ar_obj, "getHospitalPatientID", "")) if ar_obj else u""

        rows.append(dict(
            request_id=request_id,
            sample_id=sample_id,
            client=client,
            state=state,
            created=created,
            keyword=keyword or kw,
            ar_path=ar_path,
            subject_uid=subject_uid,
            interim=interim_flat,
            interims=interims_map,
            ethnic_group=enthnic_group,
            center_name=center_name,
            disease_diagnosis=disease_diagnosis,
            cancer_type=cancer_type,
            population=population,
            hospital_patient_id=hospital_patient_id,
        ))

    return rows


# ------------------------------------------------------------------
# 导入处理器注册
#   精确注册： (keyword, table) -> func
#   模糊匹配注册：matcher(keyword)->bool + table  -> func
#      用于“一类关键字（如以 hla 开头）都走同一处理器”，避免手工映射
# ------------------------------------------------------------------

IMPORT_HANDLERS = {}
IMPORT_MATCHERS = []

Handler = namedtuple("Handler", ["experiment", "table", "preview", "execute", "describe"])
_HANDLER_REGISTRY = {}  # key=(experiment, table)


def register_handler(experiment, table):
    """把处理器注册为 (experiment, table) → handler(含 preview/execute/describe)"""

    def _decorator(funcs):
        preview = getattr(funcs, "preview", None)
        execute = getattr(funcs, "execute", None)
        describe = getattr(funcs, "describe", lambda: u"")
        if not callable(preview) or not callable(execute):
            raise ValueError("Handler must provide callable 'preview' 和 'execute'")
        _HANDLER_REGISTRY[(experiment, table)] = Handler(experiment, table, preview, execute, describe)
        return funcs

    return _decorator


def resolve_handler(experiment, table):
    h = _HANDLER_REGISTRY.get((experiment, table))
    if h:
        return h
    for (exp, tab), h in _HANDLER_REGISTRY.items():
        if tab == table and (exp is None or exp == experiment):
            return h
    raise LookupError(u"未找到对应导入处理器：experiment=%s, table=%s" % (experiment, table))


def list_tables_for(keyword=None):
    if not _HANDLER_REGISTRY:
        return []
    tables = set()
    if keyword:
        for (exp, tab) in _HANDLER_REGISTRY:
            if exp in (keyword, None):
                tables.add(tab)
    else:
        for (_exp, tab) in _HANDLER_REGISTRY:
            tables.add(tab)
    return sorted(tables)


def list_samples_for(experiment, context, params):
    return samples_by_keyword(context, experiment, params)


def _to_text(v):
    if v is None:
        return u""
    # 数字/布尔
    if isinstance(v, (int, long, float, bool)):
        return unicode(v)
    # 列表/字典：转JSON
    if isinstance(v, (list, tuple, dict)):
        try:
            return safe_unicode(json.dumps(v, ensure_ascii=False, default=lambda o: safe_unicode(unicode(o))))
        except Exception:
            return safe_unicode(unicode(v))
    try:
        return safe_unicode(v)
    except Exception:
        try:
            return unicode(v, "utf-8", "ignore")
        except Exception:
            return u""


def _interims_kv(analysis_obj, use_title_first=True):
    """把 Analysis 的结果扁平成 OrderedDict({列名: 值})；按服务里配置的顺序输出。"""
    kv = OrderedDict()
    if not analysis_obj:
        return kv

    try:
        if hasattr(analysis_obj, "getResult"):
            main_res = analysis_obj.getResult()
            if main_res not in (None, ""):
                svc = getattr(analysis_obj, "getService", lambda: None)()
                title = _to_text(getattr(svc, "Title", lambda: "")())
                kw = _to_text(getattr(svc, "getKeyword", lambda: "")())
                main_key = title or kw or u"result"
                kv[main_key] = _to_text(main_res)
    except Exception:
        pass

    try:
        fields = []
        if hasattr(analysis_obj, "getInterimFields"):
            fields = analysis_obj.getInterimFields() or []
        for f in fields:
            raw_key = f.get("title") or f.get("label") if use_title_first else f.get("keyword")
            if not raw_key:
                raw_key = f.get("keyword") or u"-"
            key = _to_text(raw_key).strip().rstrip(u":：")
            val = _to_text(f.get("value"))
            if key not in kv:
                kv[key] = val
    except Exception:
        pass

    return kv


def _has_meaningful_interims(obj):
    """对象/其父对象上，是否存在至少一个“有值”的 interim 字段"""

    def _iter_interims(o):
        gf = getattr(o, "getInterimFields", None)
        if callable(gf):
            try:
                data = gf() or []
                return data if isinstance(data, (list, tuple)) else []
            except Exception:
                return []
        return []

    fields = _iter_interims(obj)
    if not fields and getattr(obj, "aq_parent", None) is not None:
        fields = _iter_interims(obj.aq_parent)
    for f in fields:
        v = f.get("value")
        if v is None:
            continue
        if isinstance(v, basestring):
            if v.strip():
                return True
        else:
            return True
    return False


def _resolve_analysis_obj_fallback(site, ar_path, keyword=None):
    try:
        ar = site.unrestrictedTraverse(ar_path.lstrip("/"))
    except Exception:
        ar = None
    if ar is None:
        return None

    # 取所有 Analysis 子对象，按 modified 倒序 + id 数字后缀倒序
    children = [c for c in getattr(ar, "objectValues", lambda *a, **k: [])("Analysis")]
    if not children:
        return None

    def _ok_state(o):
        bad = set(["retracted", "invalid", "cancelled", "rejected", "invalidated", "retracted_state"])
        st = getattr(o, "review_state", "") or getattr(o, "getReviewState", lambda: "")()
        return st not in bad

    def _key(o):
        try:
            m = getattr(o, "modified", None)
            mv = m and m() or None
        except Exception:
            mv = None
        _id = getattr(o, "id", "")
        suf = -1
        if isinstance(_id, basestring) and "-" in _id:
            try:
                suf = int(_id.rsplit("-", 1)[-1])
            except Exception:
                suf = -1
        return (mv, suf)

    children.sort(key=_key, reverse=True)

    # 匹配 keyword且状态有效且有值
    if keyword:
        for c in children:
            try:
                getter = getattr(c, "getAnalysisService", None)
                svc = getter() if callable(getter) else getattr(c, "getService", lambda: None)()
                kw = getattr(svc, "getKeyword", lambda: None)() or ""
            except Exception:
                kw = ""
            if kw == keyword and _ok_state(c) and _has_meaningful_interims(c):
                return c

    # 状态有效且有值
    for c in children:
        if _ok_state(c) and _has_meaningful_interims(c):
            return c

    # 状态有效
    for c in children:
        if _ok_state(c):
            return c

    return children[0]


@register_handler("hla-bioinformatics", "hlav1")
class HlaV1Handler(object):
    """(experiment='hla-bioinformatics', table='hlav1') 的导入处理器"""

    @staticmethod
    def describe():
        return u"将 HLA 结果扁平化导入表 hlav1（唯一键：sample_id+ksh_id）"

    # ---------- 工具函数 ----------
    @staticmethod
    def _safe_get(d, *keys):
        """从 dict d 中按多个候选键取值；如果 d 不是映射，直接返回 None。"""
        # 关键：容错——items 里可能混入了字符串/列表
        if not isinstance(d, dict):
            return None
        for k in keys:
            if k in d and d[k] not in (None, u"", ""):
                v = d[k]
                try:
                    is_text = isinstance(v, (unicode, str)) if str is bytes else isinstance(v, str)
                except NameError:
                    is_text = isinstance(v, str)
                if is_text:
                    v = v.strip()
                return v
        return None

    @staticmethod
    def _get_from(row, *names):
        """
        同时从 row 顶层与 row['interims'] 查找，支持大小写/连字符与下划线的等价形式。
        取到非空值即返回（做 strip）。
        """
        if not isinstance(row, dict):
            return None

        pools = [row, row.get("interims") or {}]

        def cand(n):
            n2 = n.replace("-", "_")
            return (n, n.lower(), n2, n2.lower())

        for name in names:
            for p in pools:
                for key in cand(name):
                    if key in p:
                        v = p.get(key)
                        if v not in (None, u"", ""):
                            try:
                                is_text = isinstance(v, (unicode, str)) if str is bytes else isinstance(v, str)
                            except NameError:
                                is_text = isinstance(v, str)
                            return v.strip() if is_text else v
        return None

    @staticmethod
    def _normalize_hla_tokens(val):
        """
        把形如 "A*24:02:01;A*31:01:01" 规范成 ["A*24:02","A*31:01"]；
        - 忽略 NA（大小写均可）
        - 去掉引号、空白
        """
        if val is None:
            return []
        s = unicode(val) if str is bytes else str(val)
        s = s.strip().strip("u'").strip("'").strip('"')
        tokens = [t.strip() for t in s.split(";") if t.strip()]
        out = []
        for t in tokens:
            if t.upper() == "NA":
                continue
            if "*" in t:
                locus, right = t.split("*", 1)
                parts = [p for p in right.split(":") if p]
                if len(parts) >= 2:
                    right = parts[0] + ":" + parts[1]
                else:
                    right = parts[0] if parts else ""
                norm = u"%s*%s" % (locus, right) if right else t
                out.append(norm)
            else:
                # 没有 * 的保留原样（极端兼容）
                out.append(t)
        return out

    @staticmethod
    def _build_hla_field(row):
        """
        只拼 A/B/C 三个位点；格式如：
        A*24:02;A*31:01;B*40:02;B*40:01;C*07:02;C*03:04
        """
        alleles = []
        for key in (u"HLA-A", u"HLA-B", u"HLA-C"):
            raw = HlaV1Handler._get_from(row, key)  # 同时查顶层和 interims
            alleles.extend(HlaV1Handler._normalize_hla_tokens(raw))
        hla = u";".join(alleles) if alleles else None
        if hla and len(hla) > 255:
            hla = hla[:255]
        return hla

    @staticmethod
    def _find_kxv_sample_id(site, hospital_patient_id):
        if not hospital_patient_id:
            return None

        # 清理引号（兼容存储带引号的情况）
        hpid = hospital_patient_id.strip().strip("'").strip('"')
        if not hpid:
            return None

        try:
            # 直接遍历上海六院 client 下所有 AR
            client = site.unrestrictedTraverse("clients/client-23")
            for ar_id in client.objectIds("AnalysisRequest"):
                try:
                    a = client[ar_id]
                    a_hpid = a.getHospitalPatientID()
                    if not a_hpid:
                        continue
                    a_hpid = str(a_hpid).strip().strip("'").strip('"')
                    if a_hpid != hpid:
                        continue
                    uid = a.getSubjectUID() if hasattr(a, "getSubjectUID") else None
                    if uid and str(uid).upper().startswith("KXV"):
                        # LOG.info("[find_kxv] 找到 KXV: ar=%s uid=%s", ar_id, uid)
                        return str(uid)
                except Exception:
                    continue
        except Exception as e:
            LOG.info("[find_kxv] 遍历出错: %r", e)

        return None

    @classmethod
    def _row_to_hlav1(cls, row,site=None):
        """
        samples_by_keyword 的一行 → hlav1 行：
        """
        S = lambda *k: cls._get_from(row, *k)
        original_sample_id = S("SubjectUID", "subject_uid", "subjectuid")
        sample_id = original_sample_id  # 默认用原始值
        tumor_type = S("tumor", "result_tumor")
        total_cellnum = S("total_cellnum", "result_total_cellnum")
        source = S("source", "result_source")
        tissue = S("tissue", "result_tissue")
        tissue_type = S("tissue_type", "result_tissue_type")
        patient_id = S("patientID", "patient_id", "result_patientid")
        ksh_id = S("KshId", "kshid", "result_kshId", "result_kshid")
        center_name = S("center_name", "client")
        disease_diagnosis = S("disease_diagnosis")
        hospital_patient_id = row.get("hospital_patient_id") or u""

        if site is not None and center_name == u"上海六院" and hospital_patient_id:
            kxv_id = cls._find_kxv_sample_id(site, hospital_patient_id)
            if kxv_id:
                sample_id = kxv_id
            else:
                LOG.info("[row_to_hlav1] 未找到 KXV，保留原始 sample_id=%r", sample_id)
        else:
            LOG.info("[row_to_hlav1] 不满足上海六院条件，跳过KXV查找: site=%s center=%r hpid=%r",
                     "有" if site is not None else "无", center_name, hospital_patient_id)

        hla = cls._build_hla_field(row)
        hla_cellratio = S("hla_cellratio", "HLA Cell Ratio", "cellratio")
        hla_expratio = S("hla_expratio", "HLA Exp Ratio", "expratio")
        relative_exp = S("relative_exp", "Relative Exp")
        nation = S("ethnic_group", "nation")
        population = S("population")
        cancer = S("cancer_type")

        return dict(
            sample_id=sample_id,
            original_sample_id=original_sample_id,
            tumor_type=tumor_type,
            hla=hla,
            hla_cellratio=hla_cellratio,
            hla_expratio=hla_expratio,
            total_cellnum=total_cellnum,
            source=source,
            tissue_type=tissue_type,
            relative_exp=relative_exp,
            patient_id=patient_id,
            population=population,
            ksh_id=ksh_id,
            tissue=tissue,
            nation=nation,
            center_name=center_name,
            cancer=cancer,
            disease_diagnosis=disease_diagnosis,
            hospital_patient_id=hospital_patient_id,
        )

    # ---------- 预览 ----------
    @staticmethod
    def preview(site, params):

        from collections import OrderedDict
        keyword = params.get("keyword")
        limit = int(params.get("limit") or 100)
        review_state = params.get("review_state")

        LOG.info("[HLAv1] preview start: experiment=%s table=%s limit=%s review_state=%s",
                 params.get("keyword"), params.get("table"), limit, review_state)

        items = samples_by_keyword(
            site, keyword=keyword,
            params={"limit": limit, "review_state": review_state}
        )

        LOG.info("[HLAv1] fetched items: %s", len(items))
        if items:
            LOG.debug("[HLAv1] first item snapshot: %s", _peek_row(items[0]))
            if len(items) > 1:
                LOG.debug("[HLAv1] second item snapshot: %s", _peek_row(items[1]))

        # 组装预览行：固定列 + 扁平化结果列
        preferred = ["sample_id", "subject_uid", "ethnic_group", "center_name", "disease_diagnosis"]
        seen_cols, columns, preview_rows = set(), [], []
        for c in preferred:
            columns.append(c);
            seen_cols.add(c)

        for it in items:
            row = OrderedDict()
            row["sample_id"] = it.get("sample_id") or it.get("request_id") or u""
            row["subject_uid"] = it.get("subject_uid") or u""
            row["ethnic_group"] = it.get("ethnic_group") or u""
            row["center_name"] = it.get("center_name") or it.get("client") or u""
            row["disease_diagnosis"] = it.get("disease_diagnosis") or u""

            # 结果列
            interims = it.get("interims") or OrderedDict()
            for k, v in interims.items():
                key = _to_text(k).strip().rstrip(u":：")
                if key in preferred:
                    key = key + u"_value"  # 防撞列名
                row[key] = _to_text(v)
                if key not in seen_cols:
                    columns.append(key);
                    seen_cols.add(key)

            preview_rows.append(row)

        return {
            "columns": columns,
            "rows": preview_rows[:100],
            "handled": len(preview_rows),
            "dry_run": True,
        }

    # ---------- 真正落库 ----------
    @classmethod
    def execute(cls, site, params):
        """
        把数据写入 MySQL.hlav1
        - 批量 upsert：INSERT ... ON DUPLICATE KEY UPDATE
        - 唯一键：(sample_id, ksh_id)
        """
        keyword = params.get("keyword")
        try:
            limit = int(params.get("limit")) if params.get("limit") else None
        except Exception:
            limit = None
        review_state = params.get("review_state")

        # 1 取数
        items = samples_by_keyword(
            site, keyword=keyword, params={"limit": limit, "review_state": review_state},
        )

        # 2 映射为 hlav1 行
        # mapped = (cls._row_to_hlav1(r) for r in items)

        # 3 过滤掉缺少唯一键的行（sample_id 或 ksh_id 缺失）
        rows = []
        skipped = 0
        miss_sample = 0
        miss_ksh = 0
        bad_type = 0
        map_errors = 0
        sample_examples = []
        ksh_examples = []

        for it in items:
            if not isinstance(it, dict):
                bad_type += 1
                skipped += 1
                LOG.warning("[HLAv1] skip non-dict item: %s", _short(it))
                continue
            try:
                r = cls._row_to_hlav1(it, site=site) # 新增传入site
            except Exception as e:
                map_errors += 1
                skipped += 1
                LOG.exception("[HLAv1] mapping error for item: %s", _peek_row(it))
                continue

            if not r.get("sample_id"):
                miss_sample += 1
                skipped += 1
                if len(sample_examples) < 3:
                    sample_examples.append(_peek_row(it))
                continue

            if not r.get("ksh_id"):
                miss_ksh += 1
                skipped += 1
                if len(ksh_examples) < 3:
                    ksh_examples.append(_peek_row(it))
                continue

            rows.append(r)

        if sample_examples:
            LOG.info("[HLAv1] examples missing sample_id (≤3): %s", sample_examples)
        if ksh_examples:
            LOG.info("[HLAv1] examples missing ksh_id (≤3): %s", ksh_examples)

        if not rows:
            return {
                "inserted": 0,
                "handled": 0,
                "skipped": skipped,
                "miss_sample": miss_sample,
                "miss_ksh": miss_ksh,
                "dry_run": False,
            }

        # ---- 走到这里说明 rows 非空，开始入库并在入库前/后打日志 ----

        # 目标列顺序（与 SQL 占位符顺序一致）
        cols = [
            "sample_id","original_sample_id", "tumor_type", "hla", "hla_cellratio", "hla_expratio",
            "total_cellnum", "source", "tissue_type", "relative_exp",
            "patient_id", "population", "ksh_id", "tissue", "nation",
            "center_name", "cancer", "disease_diagnosis", "hospital_patient_id",
        ]
        values = [tuple(r.get(c) for c in cols) for r in rows]

        # 入库前日志：列名 + 第一条 value 样例
        LOG.debug("[HLAv1] upsert cols: %s", cols)
        LOG.debug("[HLAv1] first value tuple: %s", values[0] if values else "[]")

        # 执行批量 upsert
        inserted = mysql_db.bulk_upsert("hlav1", cols, values, uniq=("sample_id", "ksh_id"))

        # 入库后日志
        LOG.info("[HLAv1] upsert done: tried=%d, inserted=%d", len(values), inserted)

        # 统一返回给视图层
        return {
            "inserted": inserted,
            "handled": len(rows),
            "skipped": skipped,
            "miss_sample": miss_sample,
            "miss_ksh": miss_ksh,
            "dry_run": False,
        }


@register_handler("BL-SLRS", "bl_slrs_raw")
class BlSlrsRawHandler(object):
    """(experiment='BL-SLRS', table='bl_slrs_raw') 导入处理器：一行一个KSU(sample_id)，结果JSON入库"""

    @staticmethod
    def describe():
        return u"将 BL-SLRS 结果以 JSON 形式导入表 bl_slrs_raw（唯一键：KSU sample_id）"

    @staticmethod
    def _get_from(row, *names):
        if not isinstance(row, dict):
            return None
        pools = [row, row.get("interims") or {}]

        def cand(n):
            n2 = n.replace("-", "_")
            return (n, n.lower(), n2, n2.lower())

        for name in names:
            for p in pools:
                for key in cand(name):
                    if key in p:
                        v = p.get(key)
                        if v not in (None, u"", ""):
                            try:
                                is_text = isinstance(v, (unicode, str)) if str is bytes else isinstance(v, str)
                            except NameError:
                                is_text = isinstance(v, str)
                            return v.strip() if is_text else v
        return None

    @classmethod
    def _row_to_bl_slrs_raw(cls, row):

        system_id = row.get("sample_id")
        sample_id = row.get("subject_uid")
        center_name = row.get("center_name")
        review_state = row.get("state")
        subject_uid = row.get("subject_uid")
        hospital_patient_id = row.get("hospital_patient_id") or u""
        result_json = json.dumps(row.get("interims") or {}, ensure_ascii=False)

        return dict(
            system_id=system_id,
            sample_id=sample_id,
            center_name=center_name,
            review_state=review_state,
            analysis_uid=None,
            sample_uid=None,
            subject_uid=subject_uid,
            hospital_patient_id=hospital_patient_id,
            result_json=result_json,
        )

    # 预览
    @classmethod
    def preview(cls, site, params):
        keyword = params.get("keyword")
        limit = int(params.get("limit") or 100)
        review_state = params.get("review_state")
        items = samples_by_keyword(site, keyword=keyword, params={"limit": limit, "review_state": review_state})

        rows = []
        for it in items[:100]:
            r = cls._row_to_bl_slrs_raw(it)
            rows.append({
                "system_id": r.get("system_id"),
                "sample_id": r.get("sample_id"),
                "center_name": r.get("center_name"),
                "review_state": r.get("review_state"),
                "subject_uid": r.get("subject_uid"),
            })

        return {
            "columns": ["system_id", "sample_id", "center_name", "review_state", "subject_uid"],
            "rows": rows,
            "handled": len(items),
            "dry_run": True,
        }

    # ---------- 真正落库 ----------
    @classmethod
    def execute(cls, site, params):
        keyword = params.get("keyword")
        try:
            limit = int(params.get("limit")) if params.get("limit") else None
        except Exception:
            limit = None
        review_state = params.get("review_state")

        items = samples_by_keyword(site, keyword=keyword, params={"limit": limit, "review_state": review_state})

        rows = []
        skipped = 0
        miss_ksu = 0
        bad_type = 0

        for it in items:
            if not isinstance(it, dict):
                bad_type += 1
                skipped += 1
                continue

            r = cls._row_to_bl_slrs_raw(it)

            if not r.get("sample_id"):
                miss_ksu += 1
                skipped += 1
                continue

            rows.append(r)

        if not rows:
            return {"inserted": 0, "handled": 0, "skipped": skipped, "miss_ksu": miss_ksu, "dry_run": False}

        cols = ["system_id", "sample_id", "center_name", "review_state",
                "analysis_uid", "sample_uid", "subject_uid", "hospital_patient_id", "result_json"]
        values = [tuple(r.get(c) for c in cols) for r in rows]

        inserted = mysql_db.bulk_upsert("bl_slrs_raw", cols, values, uniq=("sample_id",))

        return {"inserted": inserted, "handled": len(rows), "skipped": skipped, "miss_ksu": miss_ksu, "dry_run": False}
