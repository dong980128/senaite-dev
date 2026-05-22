# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import logging
from collections import defaultdict
import re

from senaite.core.mysql.db import query_all
LOG = logging.getLogger("mysql.hla.analytics")

_INVISIBLES = re.compile(u'[\u0009\u000A\u000D\u00A0\u200B\uFEFF]')
_TWO_FIELD_RE = re.compile(r'^([A-Z0-9]+)\*(\d{2}):(\d{2})(:.*)?$')

# HLA Summary module
def _norm(s):
    """清洗 allele：去 HLA-、去不可见字符、trim、大写"""
    if not s:
        return u''
    t = s.strip()
    if t[:4].upper() == 'HLA-':
        t = t[4:].strip()
    t = _INVISIBLES.sub(u'', t)
    return t.upper()

def _to_two_field_allele(s):

    t = _norm(s)  # 先去掉 HLA-、不可见字符并大写
    if not t:
        return u''
    m = _TWO_FIELD_RE.match(t)
    if m:
        return u'%s*%s:%s' % (m.group(1), m.group(2), m.group(3))
    return t


def _pct(x):  # '25.28%' -> 25.28
    try:
        return float((x or '0').rstrip('%'))
    except Exception:
        return 0.0


def _convert_map_to_list(d):
    items = [{'name': k, 'value': v} for (k, v) in d.items()]
    items.sort(key=lambda x: _pct(x.get('value')), reverse=True)
    return items


def get_hla_avg(source=None, population=None):
    """
    返回 {'A': [{'name':'A*11:01','value':'36.44%'}], 'B': [.], ...}
    逻辑：
      1）从 hlav1 取出 hla 字段（可按 source、population 过滤）
      2）每条记录：先按 ';' 切分 -> 每个等位基因截成前两段 -> 单样本内去重
      3）按样本计数：同一个样本里同一个等位基因只算 1 次
      4）按 A/B/C/DRB1/DQB1/DPB1 分桶，换算成百分比字符串
    """
    # 1) 取数
    sql = ["SELECT hla FROM hlav1"]
    params, where = [], []
    if source:
        where.append("source=%s")
        params.append(source)
    if population:
        where.append("population=%s")
        params.append(population)
    if where:
        sql.append("WHERE " + " AND ".join(where))

    rows = query_all(" ".join(sql), tuple(params))

    # 2) 有效样本（hla 非空）
    valid_rows = [r for r in rows if (r.get('hla') or u'').strip()]
    total = len(valid_rows) or 1

    # 3) 逐样本 -> 截取前两段 -> 单样本内去重 -> 计数
    from collections import defaultdict
    cnt = defaultdict(int)

    for r in valid_rows:
        raw = r.get('hla') or u''
        per_sample = set()   # 这个样本内的去重集合

        for part in raw.split(';'):
            part = part.strip()
            if not part:
                continue
            allele = _to_two_field_allele(part)
            if allele:
                per_sample.add(allele)

        for a in per_sample:
            cnt[a] += 1


    if not cnt:
        # 没有统计到任何等位基因，直接返回空桶，前端就会显示空图
        return {k: [] for k in ['A', 'B', 'C', 'DRB1', 'DQB1', 'DPB1']}

    # 4) 换算成百分比字符串
    char_avg = {
        k: u"{0:.2f}%".format(cnt[k] * 100.0 / float(total))
        for k in cnt
    }

    # 5) 分桶（前缀 startswith）
    buckets = {'A': {}, 'B': {}, 'C': {}, 'DRB1': {}, 'DQB1': {}, 'DPB1': {}}
    for k, v in char_avg.items():
        if k.startswith('A*'):
            buckets['A'][k] = v
        elif k.startswith('B*'):
            buckets['B'][k] = v
        elif k.startswith('C*'):
            buckets['C'][k] = v
        elif k.startswith('DRB1*'):
            buckets['DRB1'][k] = v
        elif k.startswith('DQB1*'):
            buckets['DQB1'][k] = v
        elif k.startswith('DPB1*'):
            buckets['DPB1'][k] = v

    # 6) 转成前端需要的 list 结构
    return {k: _convert_map_to_list(v) for k, v in buckets.items()}

def build_echarts_payload(source=None, population=None):
    """
    为前端 ECharts 组包：
    {
      "A": {"wordcloud":[{"name":"A*11:01","value":36.44},...],
            "bar":{"x":["A*11:01",...], "y":[36.44,...]}},
      ...
    }
    """
    data = get_hla_avg(source, population)
    out = {}
    for locus, items in data.items():
        names = [(it.get('name') or u'') for it in items]
        vals = [_pct(it.get('value')) for it in items]
        out[locus] = {
            'wordcloud': [{'name': n, 'value': round(v, 2)} for n, v in zip(names, vals)],
            'bar': {'x': names, 'y': [round(v, 2) for v in vals]}
        }
    return out


# HLA_Search module
def _fetch_source_rows(source):
    """
    根据分组名取行：
      - G5       -> 从 hla_G5 表取
      - BCPBMC   -> 从 hlav1 表取
      - 其它值   -> 默认从 hlav1 表取
    仅返回计算频率需要的列：sample_id, source, hla
    """
    if source == 'G5':
        sql = "SELECT sample_id, source, hla FROM hla_G5 "
        args = ()
    elif source == 'BCPBMC':
        sql = "SELECT sample_id, source, hla FROM hlav1 WHERE source='BCPBMC'"
        args = ()
    else:
        # 其它分组仍走 hlav1
        sql = "SELECT sample_id, source, hla FROM hlav1 WHERE source=%s"
        args = (source,)
    return query_all(sql, args)


def _split_hlas(s):
    return [x.strip() for x in (s or u'').split(';') if x and x.strip()]


def _to_count_map(items):
    m = defaultdict(int)
    for it in items:
        m[it] += 1
    return dict(m)


def _record_hla_count_map(hla_field):
    return _to_count_map(_split_hlas(hla_field or u''))


def _gene_frequence(rows):
    """
    计算一组记录（同一 source 组）的等位基因频率：
    - 以“样本是否包含该等位基因”为口径计数（每样本对同一等位基因只算一次）
    - 返回 {allele: 'xx.xx%'}
    """
    total = len(rows) or 1
    cnt = defaultdict(int)
    for r in rows:
        alleles = set(_split_hlas(r.get('hla') or u''))  # 样本内去重
        for a in alleles:
            cnt[a] += 1
    return {k: u"{0:.2f}%".format(cnt[k] * 100.0 / total) for k in cnt}


def _merge_two_freq_map(pbmc_map, bcpbmc_map):
    """
    合并两组频率，输出：
      { allele: { 'PBMC': 'xx.xx%', 'BCPBMC': 'yy.yy%' }, ... }
    """
    keys = set(pbmc_map.keys()) | set(bcpbmc_map.keys())
    out = {}
    for k in keys:
        out[k] = {
            'PBMC': pbmc_map.get(k, u''),
            'BCPBMC': bcpbmc_map.get(k, u'')
        }
    return out


# —— 工具 —— #
def _or_prescan_by_hla(table, hlas):
    # Py2 兼容 + 表名白名单
    if table not in ('hlav1', 'hla_g5'):
        raise ValueError('invalid table: ' + str(table))

    if not hlas:
        return []

    where = " OR ".join(["hla LIKE %s"] * len(hlas))
    sql = (
        "SELECT tumor_type, sample_id, hla, source, ksh_id "
        "FROM {table} "
        "WHERE ({where})"
    ).format(table=table, where=where)

    args = tuple("%" + h + "%" for h in hlas)
    return query_all(sql, args)


def _find_by_sample_ids(table, sample_ids_csv):
    # Py2 兼容 + 表名白名单
    if table not in ('hlav1', 'hla_g5'):
        raise ValueError('invalid table: ' + str(table))

    sql = (
        "SELECT tumor_type, sample_id, hla, source, ksh_id "
        "FROM {table} "
        "WHERE FIND_IN_SET(sample_id, %s) > 0"
    ).format(table=table)

    return query_all(sql, (sample_ids_csv,))


def _multiset_filter(rows, hlas):
    q_count = _to_count_map(hlas)
    out = []
    for obj in rows:
        m = _record_hla_count_map(obj.get('hla'))
        ok = True
        for allele, need in q_count.items():
            if m.get(allele, 0) < need:
                ok = False
                break
        if ok:
            out.append(obj)
    return out


def search_by_haplotype_or_sample(hla1=None, sample_id=None):
    """
    逻辑：
      - if hla1 给定：按 ';' 切分 -> 多重集合计数 -> 先用 OR like 预筛，再用“多重集合包含”做精过滤
      - elif sample_id 给定：FIND_IN_SET 查询
      - 不论哪种，最后都要计算 PBMC(G5) 与 BCPBMC 两组的基因频率对比

    返回：
      {
        'SampleData': [ {tumor_type,sample_id,hla,source,ksh_id,...}, ... ],
        'GeneFrequence': { allele: {'PBMC':'..%','BCPBMC':'..%'}, ... }
      }
    """

    result = {}

    # 1) 主数据：根据 hla1 或 sample_id 获取 SampleData
    if hla1:
        hlas = _split_hlas(hla1)
        if not hlas:
            result['SampleData'] = []
        else:
            rows_hlav1 = _or_prescan_by_hla('hlav1', hlas)
            rows_g5 = _or_prescan_by_hla('hla_g5', hlas)
            filt_hlav1 = _multiset_filter(rows_hlav1, hlas)
            filt_g5 = _multiset_filter(rows_g5, hlas)

            result['SampleData'] = filt_hlav1 + filt_g5

    elif sample_id:
        rows1 = _find_by_sample_ids('hlav1', sample_id)
        rows2 = _find_by_sample_ids('hla_g5', sample_id)

        result['SampleData'] = rows1 + rows2
    else:
        result['SampleData'] = []

    # 2) 统计 PBMC 与 BCPBMC 的基因频率（并行与否都可，这里保持简洁同步）
    # rows2 = query_all(
    #     "SELECT sample_id, source, hla FROM hlav1 "
    #     "WHERE source='G5' OR source='BCPBMC'", ()
    # )
    # pbmc   = [r for r in rows2 if (r.get('source') == 'G5')]
    # bcpbmc = [r for r in rows2 if (r.get('source') == 'BCPBMC')]
    #
    # pbmc_map   = _gene_frequence(pbmc)
    # bcpbmc_map = _gene_frequence(bcpbmc)

    # 2) 统计 G5 与 BCPBMC 的基因频率（来自不同表）
    pbmc = _fetch_source_rows('G5')
    bcpbmc = _fetch_source_rows('BCPBMC')

    pbmc_map = _gene_frequence(pbmc)
    bcpbmc_map = _gene_frequence(bcpbmc)

    result['GeneFrequence'] = _merge_two_freq_map(pbmc_map, bcpbmc_map)
    return result


# HLA Type module
# ========= Java前三个接口的后端计算 =========
# 1) /hl/tumorType
def _get_tumor_rows_for_java(source):
    sql = ["SELECT tumor_type, hla FROM hlav1"]
    params, where = [], []
    if source:
        where.append("source=%s");
        params.append(source)
    if where:
        sql.append("WHERE " + " AND ".join(where))
    return query_all(" ".join(sql), tuple(params))


def _top_tumor_types_by_freq_for_java(rows):
    from collections import defaultdict
    cnt = defaultdict(int)
    for r in rows:
        tt = (r.get('tumor_type') or u'').strip()
        if tt:
            cnt[tt] += 1
    ordered = sorted(cnt.items(), key=lambda x: x[1], reverse=True)
    return [k for k, _ in ordered]


def _extract_checkoptions_for_java(_rows):
    # 与 Java 约定保持一致：固定 A/B/C/DPB1/DQB1/DRB1
    return ['A', 'B', 'C', 'DPB1', 'DQB1', 'DRB1']


def get_tumor_type_java(Source=None):
    """
    返回：
    {
      "tumrType": ["...","..."],
      "CheckOptions": ["A","B","C","DPB1","DQB1","DRB1"]
    }
    """
    rows = _get_tumor_rows_for_java(Source)
    return {
        'tumrType': _top_tumor_types_by_freq_for_java(rows),
        'CheckOptions': _extract_checkoptions_for_java(rows),
    }


# 2) /hl/populations
def get_populations_java(source=None):
    """
    返回：去重并过滤 None/'None' 的 population 列表
    """
    sql = ["SELECT DISTINCT population FROM hlav1"]
    params, where = [], []
    if source:
        where.append("source=%s");
        params.append(source)
    if where:
        sql.append("WHERE " + " AND ".join(where))
    rows = query_all(" ".join(sql), tuple(params))
    out = []
    for r in rows:
        p = r.get('population')
        if p and p != 'None':
            out.append(p)
    return out


# 3) /hl/haplotype
def _fetch_haplotype_rows_java(tumorType=None, Source=None, population=None):
    sql = ["SELECT sample_id, hla, tumor_type, patient_id FROM hlav1"]
    params, where = [], []
    if tumorType:
        where.append("tumor_type=%s");
        params.append(tumorType)
    if Source:
        where.append("source=%s");
        params.append(Source)
    if population:
        where.append("population=%s");
        params.append(population)
    if where:
        sql.append("WHERE " + " AND ".join(where))
    return query_all(" ".join(sql), tuple(params))


def _distinct_by_patient_java(rows):
    seen = set()
    out = []
    for r in rows:
        pid = (r.get('patient_id') or u'').strip()
        if pid and pid not in seen:
            seen.add(pid)
            out.append(r)
    return out


def _split_hlas_java(s):
    return [x.strip() for x in (s or u'').split(';') if x and x.strip()]


def _filter_by_prefixes_java(hla_field, prefixes):
    alleles = [_norm(a) for a in _split_hlas_java(hla_field)]
    by_locus = {p: [] for p in prefixes}
    for a in alleles:
        for p in prefixes:
            if a.startswith(p + '*'):
                by_locus[p].append(a)
    for p in by_locus:
        by_locus[p] = sorted(set(by_locus[p]))
    return by_locus


def _pct_str_java(num, den):
    den = den or 1
    return u"{0:.2f}%".format(100.0 * (float(num) / float(den)))


def _pack_ratio_map_java(counter, total):
    items = sorted(counter.items(), key=lambda x: x[1], reverse=True)
    return [{'hla': k, 'percentage': _pct_str_java(v, total), 'num': u'%d/%d' % (v, total)} for k, v in items]


def haplotype_java(values=None, tumorType=None, Source=None, population=None):
    """
    对齐 Java 的 /hl/haplotype 思路（One/Two/Three 三块统计）
    返回：
    {
      'HaplotypeByOne':   [{'hla':..., 'percentage':'..%', 'num':'x/size'},...],
      'HaplotypeByTwo':   [...],
      'HaplotypeByThree': [...]
    }
    """
    prefixes = [v.strip() for v in (values or []) if v and v.strip()]
    rows = _fetch_haplotype_rows_java(tumorType, Source, population)
    distinct = _distinct_by_patient_java(rows)
    size = len(distinct) or 1

    # 每位患者，按前缀取候选等位基因
    per_patient = []
    for r in distinct:
        by_locus = _filter_by_prefixes_java(r.get('hla') or u'', prefixes)
        per_patient.append(by_locus)

    # One：单等位基因（样本内去重后，按“是否包含”计一次）
    from collections import defaultdict
    cnt_one = defaultdict(int)
    for by_locus in per_patient:
        visited = set()
        for lst in by_locus.values():
            for a in lst:
                visited.add(a)
        for a in visited:
            cnt_one[a] += 1

    # Two：把该患者在所有前缀下的等位基因做并集组合（字典序拼接）计 1 次
    cnt_two = defaultdict(int)
    for by_locus in per_patient:
        pool = []
        for p in prefixes:
            pool.extend(by_locus.get(p, []))
        if not pool:
            continue
        key = u';'.join(sorted(set(pool)))
        if key:
            cnt_two[key] += 1

    # Three：在“并集组合”的基础上“去一个”派生，逐个计 1 次
    cnt_three = defaultdict(int)
    for by_locus in per_patient:
        pool = sorted(set(sum([by_locus.get(p, []) for p in prefixes], [])))
        if len(pool) < 2:
            continue
        for i in range(len(pool)):
            key = u';'.join([pool[j] for j in range(len(pool)) if j != i])
            if key:
                cnt_three[key] += 1

    return {
        'HaplotypeByOne': _pack_ratio_map_java(cnt_one, size),
        'HaplotypeByTwo': _pack_ratio_map_java(cnt_two, size),
        'HaplotypeByThree': _pack_ratio_map_java(cnt_three, size),
    }
