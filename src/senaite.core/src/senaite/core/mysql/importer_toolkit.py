# -*- coding: utf-8 -*-
# 通用：把“实验结果字典”映射为“指定表”的行(dict)
from __future__ import unicode_literals
import re
import json

try:
    text_type = unicode
except NameError:
    text_type = str

# -------- 小工具 --------

def _to_snake(s):
    if s is None:
        return ""
    if not isinstance(s, text_type):
        s = text_type(s)
    s = s.replace("-", "_").replace(" ", "_").replace(".", "_")
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    return s.lower().strip("_")

def normalize_key(raw_key):
    """去 result_ 前缀、大小写/驼峰/连字符归一"""
    k = "" if raw_key is None else text_type(raw_key).strip()
    k = k.replace("HLA-", "HLA_")          # HLA-A -> HLA_A
    k = k.replace("patientID", "patient_id")
    k = _to_snake(k)
    if k.startswith("result_"):
        k = k[len("result_"):]
    return k

def is_na(v):
    if v is None:
        return True
    s = text_type(v).strip()
    return (s == "" or s.upper() in set(["NA", "N/A", "-"]))

def ensure_iterable(v):
    """string/list/tuple/set -> list[string]；逗号也当分号处理"""
    if v is None:
        return []
    if isinstance(v, (list, tuple, set)):
        return [text_type(x) for x in v]
    s = text_type(v).replace(",", ";")
    return [x.strip() for x in s.split(";")]

# -------- 特殊聚合列（列名 -> 合并函数） --------

_HLA_GENE_PATTERN = re.compile(r"\b(HLA[-_])?(A|B|C|DRB1|DQB1|DPB1)\b", re.IGNORECASE)

def merge_hla(all_results):
    # type: (dict) -> str
    parts = []
    for k, v in (all_results or {}).iteritems():
        if _HLA_GENE_PATTERN.search(text_type(k)):
            for token in ensure_iterable(v):
                if not is_na(token):
                    parts.append(token)
    parts = [p for p in parts if p]
    return u";".join(parts)

SPECIAL_MERGERS = {
    # 表里存在名为 hla 的列时，自动用此聚合器
    "hla": merge_hla,
    # 后续可在此注册更多聚合列
}

# -------- 表结构来源（优先 DB 反查） --------

def _quote_ident(name):
    # 简单转义反引号，避免注入
    return name.replace("`", "``")

def get_table_columns(table_name, db_conn=None, static_columns=None):
    """
    优先 SHOW COLUMNS FROM `table` 取列；失败则回退到 static_columns
    返回 snake_case 的列名集合
    """
    cols = set()
    if db_conn is not None:
        try:
            cur = db_conn.cursor()
            try:
                sql = "SHOW COLUMNS FROM `{}`".format(_quote_ident(table_name))
                cur.execute(sql)
                rows = cur.fetchall() or []
                for row in rows:
                    # 兼容 tuple/dict 两种返回
                    if isinstance(row, (list, tuple)) and len(row) > 0:
                        col = row[0]
                    else:
                        # 常见驱动字段名：Field
                        col = row.get("Field") if isinstance(row, dict) else None
                    if col:
                        cols.add(text_type(col))
            finally:
                try:
                    cur.close()
                except Exception:
                    pass
        except Exception:
            pass
    if (not cols) and static_columns:
        for c in static_columns:
            cols.add(text_type(c))
    return set([_to_snake(c) for c in cols])

# -------- 主映射函数 --------

def map_results_to_table_row(table, results_dict, table_columns,
                             extra_policy="ignore", extra_sink_column=None):
    """
    results_dict: 实验结果字典（interims / results 的合并）
    table_columns: 目标表列集合（snake_case）
    extra_policy:  "ignore" | "json"
      - "ignore": 不在表中的结果键丢弃
      - "json":   把“未入表”的键值序列化进 extra_sink_column（该列需存在）
    """
    row = {}

    # 1) 特殊聚合列（如 hla）
    for col in list(table_columns):
        if col in SPECIAL_MERGERS:
            merged = SPECIAL_MERGERS[col](results_dict)
            if merged:
                row[col] = merged

    # 2) 常规键映射
    leftovers = {}
    for raw_k, v in (results_dict or {}).iteritems():
        norm = normalize_key(raw_k)
        if norm in SPECIAL_MERGERS:
            leftovers[raw_k] = v  # 已由聚合器负责
            continue
        if norm in table_columns:
            vals = [t for t in ensure_iterable(v) if not is_na(t)]
            row[norm] = u";".join(vals) if vals else u""
        else:
            leftovers[raw_k] = v

    # 3) 表外字段处理
    if (extra_policy == "json" and extra_sink_column and
            (extra_sink_column in table_columns)):
        try:
            row[extra_sink_column] = json.dumps(leftovers, ensure_ascii=False)
        except Exception:
            row[extra_sink_column] = u""

    return row
