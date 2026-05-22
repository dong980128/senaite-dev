# -*- coding: utf-8 -*-
from __future__ import print_function

import re
import json
import time
from collections import Counter

from Products.Five import BrowserView
from senaite.core import mysql, logger
from senaite.core.mysql.utils import CenterResolver, SubjectResolver

_WHITELIST_SORT = {
    "id": "id",
    "sample_id": "sample_id",
    "hla": "hla",
    "hla_a": "hla_a",
    "hla_b": "hla_b",
    "hla_c": "hla_c",
    "tumor_type": "tumor_type",
    "cancer": "cancer",
    "disease_diagnosis": "disease_diagnosis",
    "center_name": "center_name",
    "nation": "nation",
    "population": "population",
    "ksh_id": "ksh_id",
    "tissue": "tissue",
    "tissue_type": "tissue_type",
    "source": "source",
    "mage_a4_hscore": "mage_a4_hscore",
    "ropn1_hscore": "ropn1_hscore",
    "mart_1_hscore": "mart_1_hscore",
    "afp_hscore": "afp_hscore",
    "gp100_hscore": "gp100_hscore",
    "hla_i_hscore": "hla_i_hscore",
    "cd8_hscore": "cd8_hscore",
    "ny_eso_1_hscore": "ny_eso_1_hscore",
}


def _as_int(v, default):
    try:
        return int(v)
    except Exception:
        return default


def _safe_int_or_none(v):
    try:
        if v is None or v == "":
            return None
        return int(v)
    except Exception:
        try:
            return int(float(v))
        except Exception:
            return None


def _contains_hla_token(raw, token):
    raw = (raw or "").strip()
    token = (token or "").strip()
    if not raw or not token:
        return False

    parts = [p.strip() for p in raw.split(";") if p and p.strip() and p.strip() != "-"]
    return token in parts


def _highlight_hla_token(raw, token):
    raw = raw or ""
    token = (token or "").strip()
    if not raw or not token:
        return raw

    pat = r'(^|;)\s*(' + re.sub(r'([.^$*+?{}\[\]\\|()])', r'\\\1', token) + r')\s*(?=;|$)'
    rx = re.compile(pat)

    def _repl(m):
        sep = m.group(1) or ""
        tok = m.group(2) or ""
        return u'%s<mark>%s</mark>' % (sep, tok)

    return rx.sub(_repl, raw)

# 插入数据工具
HLAV1_COLUMNS = set([
    "sample_id", "tumor_type", "hla", "hla_cellratio", "hla_expratio",
    "total_cellnum", "source", "tissue_type", "relative_exp",
    "patient_id", "population", "ksh_id", "tissue", "nation",
    "center_name", "cancer", "disease_diagnosis",
])


def _filter_row_to_hlav1(row):
    out = {}
    for c in HLAV1_COLUMNS:
        v = row.get(c, "")
        if v is None:
            v = ""
        out[c] = v
    return out


def insert_hlav1_rows(rows, upsert=False, upsert_key="sample_id"):
    """
    批量写入 hlav1。
    rows: "结果→表行"抽取器返回的字典列表（列名已是 hlav1 的）
    upsert: 为 True 时做"冲突更新"（需要数据库里对 upsert_key 有唯一键）
    upsert_key: 用哪个列作为唯一匹配键（默认 sample_id）
    返回: 插入/更新的行数
    """
    if not rows:
        return 0

    filtered = [_filter_row_to_hlav1(r) for r in rows]

    cols = sorted(list(HLAV1_COLUMNS))
    placeholders = ", ".join(["%({})s".format(c) for c in cols])
    collist = ", ".join(cols)

    if upsert:
        # 需要在 hlav1表建唯一索引: 创建的索引是： UNIQUE KEY uq_sample_id (sample_id)
        set_clause = ", ".join(["{0}=VALUES({0})".format(c) for c in cols if c != upsert_key])
        sql = (
            "INSERT INTO hlav1 ({cols}) VALUES ({ph}) "
            "ON DUPLICATE KEY UPDATE {set_clause}"
        ).format(cols=collist, ph=placeholders, set_clause=set_clause)
    else:
        sql = "INSERT INTO hlav1 ({cols}) VALUES ({ph})".format(cols=collist, ph=placeholders)

    count = 0
    try:
        if hasattr(mysql, "executemany"):
            count = mysql.executemany(sql, filtered)
        else:
            for r in filtered:
                mysql.execute(sql, r)
                count += 1
    except Exception:
        raise
    return count


# 计算筛选hla组合基因占比
def _escape_regex(s):
    return re.sub(r'([.^$*+?{}\[\]\\|()])', r'\\\1', s)


def _build_hla_clause(hla_input):
    """
    根据 HLA 输入构造 AND 组合的 REGEXP 子句
    返回: (sql_fragment, params, tokens)
    - sql_fragment: "(hla REGEXP %(hla_re_0)s AND hla REGEXP %(hla_re_1)s)"
    - params: {"hla_re_0": r"(^|;)A\*11:01(;|$)", ...}
    - tokens: 去重后保序的 token 列表
    """
    hla_input = (hla_input or "").strip()
    if not hla_input:
        return "", {}, []

    raw_tokens = [t.strip() for t in re.split(r'[;,\s|]+', hla_input) if t.strip()]
    if not raw_tokens:
        return "", {}, []

    # 去重并保序
    seen, tokens = set(), []
    for t in raw_tokens:
        if t not in seen:
            tokens.append(t)
            seen.add(t)

    parts, params = [], {}
    for idx, tok in enumerate(tokens):
        pat = r'(^|;)' + _escape_regex(tok) + r'(;|$)'
        key = 'hla_re_%d' % idx
        params[key] = pat
        parts.append("hla REGEXP %({})s".format(key))

    return "(" + " AND ".join(parts) + ")", params, tokens


def _apply_hla_col_filter(col_name, raw_input, where, params):
    raw_input = (raw_input or "").strip()
    if not raw_input:
        return

    tokens = [t.strip() for t in re.split(r'[;,\s|]+', raw_input) if t.strip()]
    if not tokens:
        return

    token_counts = Counter(tokens)

    for idx, (tok, required_count) in enumerate(token_counts.items()):
        if required_count == 1:
            # 单个 token：REGEXP 精确边界匹配
            key = '%s_re_%d' % (col_name, idx)
            params[key] = r'(^|;)' + _escape_regex(tok) + r'(;|$)'
            where.append("%s REGEXP %%(%s)s" % (col_name, key))
        else:
            # 纯合子（重复 token）：直接 LIKE 字面量匹配
            # A*02:07;A*02:07 → LIKE '%A*02:07;A*02:07%'
            literal = (';'.join([tok] * required_count))
            key = '%s_like_%d' % (col_name, idx)
            params[key] = '%%%s%%' % literal
            where.append("%s LIKE %%(%s)s" % (col_name, key))


def _compute_hla_share(base_where, base_params, hla_clause, hla_params):
    """
    计算 HLA 组合的占比：
    - all：全库分母
    - filtered：当前筛选（不含 HLA）作为分母
    """
    if not hla_clause:
        return None

    # 全库
    row_total_all = mysql.query_one("SELECT COUNT(1) AS n FROM hlav1", {}) or {"n": 0}
    row_match_all = mysql.query_one(
        "SELECT COUNT(1) AS n FROM hlav1 WHERE %s" % hla_clause,
        hla_params
    ) or {"n": 0}

    base_sql = "WHERE " + " AND ".join(base_where) if base_where else ""
    row_total_base = mysql.query_one(
        "SELECT COUNT(1) AS n FROM hlav1 %s" % base_sql,
        base_params
    ) or {"n": 0}

    where_mix = list(base_where)
    params_mix = dict(base_params)
    where_mix.append(hla_clause)
    params_mix.update(hla_params)
    mix_sql = "WHERE " + " AND ".join(where_mix)
    row_match_base = mysql.query_one(
        "SELECT COUNT(1) AS n FROM hlav1 %s" % mix_sql,
        params_mix
    ) or {"n": 0}

    def _ratio(m, t):
        return (float(m) / float(t)) if t else 0.0

    all_match, all_total = int(row_match_all["n"]), int(row_total_all["n"])
    base_match, base_total = int(row_match_base["n"]), int(row_total_base["n"])

    return {
        "all": {"match": all_match, "total": all_total, "ratio": _ratio(all_match, all_total)},
        "filtered": {"match": base_match, "total": base_total, "ratio": _ratio(base_match, base_total)},
    }


class HlaV1TableView(BrowserView):
    """浏览器视图：/@@hlav1-table
    - 远程分页/排序/筛选
    - 表头下拉筛选 distinct：?format=distinct&field=center_name
    """

    def _parse_json_param(self, v, default):
        """request.form 里可能是字符串 JSON，也可能已经是 python 对象"""
        if v is None:
            return default
        if isinstance(v, (list, dict)):
            return v
        try:
            s = (v or "").strip()
            if not s:
                return default
            return json.loads(s)
        except Exception:
            return default

    def _apply_tabulator_remote_params(self, form):
        if "page" in form or "size" in form:
            page = _as_int(form.get("page", 1), 1)
            size = _as_int(form.get("size", form.get("b_size", 20)), 20)
            if page < 1:
                page = 1
            if size < 1:
                size = 20
            form["b_size"] = size
            form["b_start"] = (page - 1) * size

        sorters = self._parse_json_param(form.get("sorters"), None)
        if not sorters:
            sorters = self._parse_json_param(form.get("sort"), [])
        if sorters and isinstance(sorters, list):
            s0 = sorters[0] or {}
            field = (s0.get("field") or "").strip()
            direction = (s0.get("dir") or s0.get("direction") or "").strip().lower()  # asc/desc
            if field:
                form["sort_on"] = field
                form["sort_order"] = "desc" if direction == "desc" else "asc"

        filters = self._parse_json_param(form.get("filters"), None)
        if not filters:
            filters = self._parse_json_param(form.get("filter"), [])
        if filters and isinstance(filters, list):
            for f in filters:
                if not isinstance(f, dict):
                    continue
                field = (f.get("field") or "").strip()
                value = f.get("value")
                if value is None:
                    continue
                value = (u"%s" % value).strip()
                if not value:
                    continue

                if field in (
                        "sample_id", "hla", "hla_a", "hla_b", "hla_c", "source", "center_name", "disease_diagnosis",
                        "tumor_type", "cancer", "nation", "tissue_type", "ksh_id", "tissue",
                        "mage_a4_hscore", "ropn1_hscore", "mart_1_hscore", "afp_hscore",
                        "gp100_hscore", "hla_i_hscore", "cd8_hscore", "ny_eso_1_hscore",
                ):
                    form[field] = value

    def _resolve_center_links(self, rows):
        if not rows:
            return {}

        names = []
        seen = set()
        for r in rows:
            cname = (r.get("center_name") or "").strip()
            if not cname:
                continue
            if cname in seen:
                continue
            seen.add(cname)
            names.append(cname)

        if not names:
            return {}

        resolver = CenterResolver(self.request)

        mapping = {}
        for cname in names:
            try:
                ret = resolver.client_info_by_center_name(cname)
            except Exception as e:
                logger.warning("[HLAV1][center_link] resolve failed center_name=%r err=%s", cname, e)
                ret = (None, None, None)

            # 兼容：有些实现可能返回 None 或长度不一致
            if not ret:
                ret = (None, None, None)
            if isinstance(ret, tuple) and len(ret) >= 3:
                client_id, client_url, samples_url = ret[0], ret[1], ret[2]
            else:
                # 最保守兜底
                try:
                    client_id, client_url, samples_url = ret
                except Exception:
                    client_id, client_url, samples_url = (None, None, None)
            mapping[cname] = (client_id, client_url, samples_url)
        return mapping

    def _resolve_subject_links(self, rows):
        """把 sample_id -> (subject_uid, subject_url) 做去重查询缓存"""
        if not rows:
            return {}
        ids = []
        seen = set()
        for r in rows:
            sid = (r.get("sample_id") or "").strip()
            if not sid:
                continue
            if sid in seen:
                continue
            seen.add(sid)
            ids.append(sid)
        if not ids:
            return {}
        sresolver = SubjectResolver(self.request)

        mapping = {}
        for sid in ids:
            try:
                ret = sresolver.subject_info_by_sample_id(sid)
            except Exception as e:
                logger.warning("[HLAV1][subject_link] resolve failed sample_id=%r err=%s", sid, e)
                ret = (None, None)

            if not ret:
                ret = (None, None)
            if isinstance(ret, tuple) and len(ret) >= 2:
                subject_uid, subject_url = ret[0], ret[1]
            else:
                try:
                    subject_uid, subject_url = ret
                except Exception:
                    subject_uid, subject_url = (None, None)

            mapping[sid] = (subject_uid, subject_url)

        return mapping

    def update(self):
        req = self.request
        form = req.form

        # 先把 tabulator 的 page/sort/filter 映射到后端支持的参数
        self._apply_tabulator_remote_params(form)

        # 分页
        self.b_size = _as_int(form.get("b_size", 20), 20)
        self.b_start = _as_int(form.get("b_start", form.get("list_bstart", 0)), 0)
        if self.b_size > 200:
            self.b_size = 200
        if self.b_start < 0:
            self.b_start = 0

        # 排序
        sort_by = (form.get("sort_on") or "id").strip()
        self.sort_on = _WHITELIST_SORT.get(sort_by, "id")
        self.sort_order = "DESC" if (form.get("sort_order") or "desc").lower() == "desc" else "ASC"

        # 过滤参数读取
        q = (form.get("q") or "").strip()
        sample_id = (form.get("sample_id") or "").strip()
        hla_a = (form.get("hla_a") or "").strip()
        hla_b = (form.get("hla_b") or "").strip()
        hla_c = (form.get("hla_c") or "").strip()
        tumor_type = (form.get("tumor_type") or "").strip()
        cancer = (form.get("cancer") or "").strip()
        disease = (form.get("disease_diagnosis") or "").strip()

        # 下拉筛选字段
        source = (form.get("source") or "").strip()
        tissue_type = (form.get("tissue_type") or "").strip()
        nation = (form.get("nation") or "").strip()
        center_name = (form.get("center_name") or "").strip()
        ksh_id = (form.get("ksh_id") or "").strip()
        tissue = (form.get("tissue") or "").strip()

        base_where = []
        base_params = {}

        if q:
            base_where.append(
                "(sample_id LIKE %(q)s "
                "OR hla LIKE %(q)s "
                "OR hla_a LIKE %(q)s "
                "OR hla_b LIKE %(q)s "
                "OR hla_c LIKE %(q)s "
                "OR source LIKE %(q)s "
                "OR center_name LIKE %(q)s "
                "OR disease_diagnosis LIKE %(q)s)"
            )
            base_params["q"] = "%%%s%%" % q

        if sample_id:
            base_where.append("sample_id LIKE %(sample_id)s")
            base_params["sample_id"] = "%%%s%%" % sample_id

        if tumor_type:
            base_where.append("tumor_type = %(tumor_type)s")
            base_params["tumor_type"] = tumor_type

        if cancer:
            base_where.append("cancer LIKE %(cancer)s")
            base_params["cancer"] = "%%%s%%" % cancer

        if disease:
            base_where.append("disease_diagnosis LIKE %(disease)s")
            base_params["disease"] = "%%%s%%" % disease

        if source:
            base_where.append("source = %(source)s")
            base_params["source"] = source
        if tissue_type:
            base_where.append("tissue_type = %(tissue_type)s")
            base_params["tissue_type"] = tissue_type
        if nation:
            base_where.append("nation = %(nation)s")
            base_params["nation"] = nation
        if center_name:
            base_where.append("center_name = %(center_name)s")
            base_params["center_name"] = center_name
        if ksh_id:
            base_where.append("ksh_id = %(ksh_id)s")
            base_params["ksh_id"] = ksh_id
        if tissue:
            base_where.append("tissue = %(tissue)s")
            base_params["tissue"] = tissue

        # HLA 列：REGEXP 精确边界匹配，单独一组
        hla_where = []
        hla_params = {}
        _apply_hla_col_filter("hla_a", hla_a, hla_where, hla_params)
        _apply_hla_col_filter("hla_b", hla_b, hla_where, hla_params)
        _apply_hla_col_filter("hla_c", hla_c, hla_where, hla_params)

        # 合并给 SQL 查询用
        where = base_where + hla_where
        params = dict(base_params)
        params.update(hla_params)

        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        order_sql = "ORDER BY %s %s" % (self.sort_on, self.sort_order)

        TABLE = "hlav1_bl_slrs_wide"

        # 总数（所有条件，含 HLA）
        cnt_sql = "select count(1) as n from %s %s" % (TABLE, where_sql)
        row = mysql.query_one(cnt_sql, params)
        self.total = int(row["n"] if row and "n" in row else 0)

        # 全库总数（无任何筛选）
        row_all = mysql.query_one("select count(1) as n from %s" % TABLE, {}) or {"n": 0}
        self.total_all = int(row_all.get("n", 0) or 0)

        has_hla_filter = bool(hla_a or hla_b or hla_c)
        if has_hla_filter:
            # 分子直接用已算好的 self.total
            self.hla_matched_all = self.total

            # 分母：只用 base 条件查一次
            if base_where:
                base_sql = "WHERE " + " AND ".join(base_where)
                row_base = mysql.query_one(
                    "SELECT COUNT(1) AS n FROM %s %s" % (TABLE, base_sql),
                    base_params
                ) or {"n": 0}
                self.hla_base_total = int(row_base["n"])
            else:
                # 没有其他筛选条件，分母就是全库
                self.hla_base_total = self.total_all

            self.hla_ratio_all = (
                float(self.hla_matched_all) / float(self.hla_base_total)
                if self.hla_base_total else 0.0
            )

            # 给前端显示的查询文本
            query_parts = []
            if hla_a:
                query_parts.append(u"HLA-A: %s" % hla_a)
            if hla_b:
                query_parts.append(u"HLA-B: %s" % hla_b)
            if hla_c:
                query_parts.append(u"HLA-C: %s" % hla_c)
            self.hla_query_display = u" | ".join(query_parts)
        else:
            self.hla_matched_all = None
            self.hla_base_total = self.total_all
            self.hla_ratio_all = None
            self.hla_query_display = u""

        # 数据
        data_sql = (
                "SELECT "
                "id, sample_id, tumor_type, "
                "hla_a, hla_b, hla_c, "
                "hla, hla_cellratio, hla_expratio, total_cellnum, "
                "source, tissue_type, relative_exp, patient_id, population, ksh_id, tissue, nation, "
                "center_name, cancer, disease_diagnosis, "
                "mage_a4_detail, mage_a4_hscore, "
                "ropn1_detail, ropn1_hscore, "
                "mart_1_detail, mart_1_hscore, "
                "afp_detail, afp_hscore, "
                "gp100_detail, gp100_hscore, "
                "hla_i_detail, hla_i_hscore, "
                "cd8_detail, cd8_hscore, "
                "ny_eso_1_detail, ny_eso_1_hscore "
                "FROM %s %s %s LIMIT %%(_limit)s OFFSET %%(_offset)s"
                % (TABLE, where_sql, order_sql)
        )

        qparams = params.copy()
        qparams["_limit"] = self.b_size
        qparams["_offset"] = self.b_start

        self.rows = mysql.query_all(data_sql, qparams)

        # ---- 批量解析跳转----
        center_map = self._resolve_center_links(self.rows)
        subject_map = self._resolve_subject_links(self.rows)

        for row in (self.rows or []):
            cname = (row.get("center_name") or "").strip()
            client_id, client_url, samples_url = center_map.get(cname, (None, None, None))
            row["center_client_id"] = client_id
            row["center_url"] = client_url
            row["center_samples_url"] = samples_url

            sid = (row.get("sample_id") or "").strip()
            subject_uid, subject_url = subject_map.get(sid, (None, None))
            row["subject_uid"] = subject_uid
            row["subject_url"] = subject_url

        # 分页
        self.next_start = self.b_start + self.b_size
        self.prev_start = max(0, self.b_start - self.b_size)

        # HLA-A / HLA-B / HLA-C 高亮显示
        def _build_highlighter(raw_input):
            raw_input = (raw_input or "").strip()
            tokens = [t.strip() for t in re.split(r'[;,\s|]+', raw_input) if t.strip()]

            seen = set()
            qtokens = []
            for t in tokens:
                if t not in seen:
                    qtokens.append(t)
                    seen.add(t)

            if not qtokens:
                return None

            def _esc(s):
                return re.sub(r'([.^$*+?{}\[\]\\|()])', r'\\\1', s)

            pattern = r'(^|;)\s*(' + "|".join(_esc(t) for t in qtokens) + r')\s*(?=;|$)'
            rx = re.compile(pattern)

            def _hl(s):
                if not s:
                    return s

                def _repl(m):
                    sep = m.group(1) or ''
                    tok = m.group(2)
                    return u'%s<mark>%s</mark>' % (sep, tok)

                return rx.sub(_repl, s)

            return _hl

        hl_a = _build_highlighter(hla_a)
        hl_b = _build_highlighter(hla_b)
        hl_c = _build_highlighter(hla_c)

        for row in self.rows:
            raw_a = row.get("hla_a", "") or ""
            raw_b = row.get("hla_b", "") or ""
            raw_c = row.get("hla_c", "") or ""
            row["highlighted_hla_a"] = hl_a(raw_a) if hl_a else raw_a
            row["highlighted_hla_b"] = hl_b(raw_b) if hl_b else raw_b
            row["highlighted_hla_c"] = hl_c(raw_c) if hl_c else raw_c

        RULE_HLA_A_TOKEN = "A*02:01"
        RULE_SCORE_THRESHOLD = 30
        SCORE_FIELDS = [
            "mage_a4_hscore",
            "ropn1_hscore",
            "mart_1_hscore",
            "afp_hscore",
            "gp100_hscore",
            "hla_i_hscore",
            "cd8_hscore",
            "ny_eso_1_hscore",
        ]

        for row in self.rows:
            raw_a = row.get("hla_a", "") or ""

            # 是否包含 A*02:01
            has_rule_a = _contains_hla_token(raw_a, RULE_HLA_A_TOKEN)

            # 哪些 score 命中 >= 30
            hit_score_fields = []
            hit_score_items = []
            for sf in SCORE_FIELDS:
                score_val = _safe_int_or_none(row.get(sf))
                if score_val is not None and score_val >= RULE_SCORE_THRESHOLD:
                    hit_score_fields.append(sf)
                    hit_score_items.append({
                        "field": sf,
                        "value": score_val,
                    })

            rule_hit = bool(has_rule_a and hit_score_fields)

            row["rule_hit"] = rule_hit
            row["rule_hla_token"] = RULE_HLA_A_TOKEN
            row["rule_score_threshold"] = RULE_SCORE_THRESHOLD
            row["rule_hit_score_fields"] = hit_score_fields
            row["rule_hit_score_items"] = hit_score_items
            row["rule_hit_score_text"] = u", ".join(
                [u"%s=%s" % (it["field"], it["value"]) for it in hit_score_items]
            ) if hit_score_items else u""

            # 给 HLA-A 单元格一个"业务规则版"高亮文本
            if rule_hit:
                row["highlighted_hla_a_rule"] = _highlight_hla_token(raw_a, RULE_HLA_A_TOKEN)
            else:
                row["highlighted_hla_a_rule"] = row.get("highlighted_hla_a", raw_a)

            # 给前端一个更友好的提示文本
            if rule_hit:
                row["rule_hit_tip"] = u"命中规则：HLA-A 包含 %s，且 %s" % (
                    RULE_HLA_A_TOKEN,
                    row["rule_hit_score_text"] or u"存在 Hscore ≥ %s" % RULE_SCORE_THRESHOLD,
                )
            else:
                row["rule_hit_tip"] = u""


    def _as_tabulator_json(self):
        page_size = self.b_size or 20
        last_page = int((self.total + page_size - 1) / page_size) if page_size else 1
        page = int((self.b_start / page_size) + 1) if page_size else 1

        payload = {
            "data": self.rows or [],
            "total": self.total,
            "page": page,
            "last_page": last_page,
            "stats": {
                # hla_query_display 优先，没有 HLA 筛选时兜底用旧的 hla 参数
                "hla_query": self.hla_query_display or (
                    (self.request.get("hla") or self.request.form.get("hla") or "")
                ).strip(),
                "hla_matched_all": self.hla_matched_all,
                "hla_base_total": self.hla_base_total,   # 分母（前端可用于显示 x/y）
                "total_all": self.total_all,
                "hla_ratio_all": self.hla_ratio_all,
            },
        }
        self.request.response.setHeader("Content-Type", "application/json; charset=utf-8")
        return json.dumps(payload, ensure_ascii=False)

    def __call__(self):
        self.update()
        fmt = (self.request.form.get("format") or "").strip().lower()
        # 表头下拉筛选用：返回某一列的唯一值
        if fmt == "distinct":
            field = (self.request.form.get("field") or "")
            field = field.strip()

            # 允许的列（严格白名单，避免 SQL 注入）
            allowed = set([
                "tumor_type", "source", "tissue_type", "nation",
                "center_name", "cancer", "disease_diagnosis", "population",
                "ksh_id", "tissue",
            ])

            if field not in allowed:
                self.request.response.setStatus(400)
                self.request.response.setHeader("Content-Type", "application/json; charset=utf-8")
                return json.dumps({"error": "field not allowed", "field": field, "allowed": sorted(list(allowed))})

            sql = (
                "SELECT DISTINCT {0} AS v FROM hlav1 "
                "WHERE {0} IS NOT NULL AND {0}<>'' "
                "ORDER BY {0} ASC LIMIT 5000"
            ).format(field)

            rows = mysql.query_all(sql, {}) or []
            values = [r.get("v") for r in rows if r.get("v")]

            self.request.response.setHeader("Content-Type", "application/json; charset=utf-8")
            return json.dumps({"field": field, "values": values}, ensure_ascii=False)

        if fmt == "stats":
            payload = {
                "hla_query": self.hla_query_display or (
                    (self.request.get("hla") or self.request.form.get("hla") or "")
                ).strip(),
                "hla_matched_all": self.hla_matched_all,
                "hla_base_total": self.hla_base_total,
                "total_all": self.total_all,
                "hla_ratio_all": self.hla_ratio_all,
            }
            self.request.response.setHeader("Content-Type", "application/json; charset=utf-8")
            return json.dumps(payload, ensure_ascii=False)

        if fmt == "json":
            return self._as_tabulator_json()

        return self.index()