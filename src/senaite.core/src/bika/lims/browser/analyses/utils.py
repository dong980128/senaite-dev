# -*- coding: utf-8 -*-
from __future__ import absolute_import

import json
import re
from collections import OrderedDict

from Products.CMFPlone.utils import safe_unicode
from bika.lims import logger

SPECIAL_RESULT_TYPES = set([
    u"tiered_multivalue",
    u"multivalue:tiered",
    # u"tcr_selector",
    # u"tcr_preparation",
    # u"posneg_with_note",
])

# condition.value分隔符
_SPLIT_RE = re.compile(u"[,\n\r\t|;，、；]+")

# BOM
_BOM = u"\ufeff"


def u(x):
    try:
        s = safe_unicode(x)
    except Exception:
        try:
            if isinstance(x, unicode):
                s = x
            elif isinstance(x, str):
                try:
                    s = x.decode("utf-8")
                except Exception:
                    s = x.decode("latin-1", "ignore")
            else:
                s = unicode(x)
        except Exception:
            s = u""

    if s and s[:1] == _BOM:
        s = s.lstrip(_BOM)
    return s


def json_loads_safe(v):
    try:
        s = u(v).strip()
        if not s:
            return None
        return json.loads(s)
    except Exception:
        return None


def norm_token(s):
    """用于比较：strip + 去掉 result_ 前缀。"""
    s = u(s).strip()
    if not s:
        return u""
    if s.lower().startswith(u"result_"):
        s = s[len(u"result_"):]
    return s.strip()


def _get_result_type(field):
    field = field or {}
    rt = field.get("result_type") or field.get("type") or field.get("result-type") or ""
    return u(rt).strip().lower()


def is_multi_interim(interim):
    rt = _get_result_type(interim)
    return rt.startswith("multi")


def get_interim_choices(interim):
    interim = interim or {}
    choices = interim.get("choices")
    if not choices:
        return None

    items = u(choices).split("|")
    pairs = []
    for it in items:
        it = u(it).strip()
        if not it:
            continue
        if ":" in it:
            k, v = it.split(":", 1)
            pairs.append((u(k).strip(), u(v).strip()))
        else:
            # 兼容只写值的情况： "A|B|C"
            pairs.append((it, it))
    return OrderedDict(pairs)


def _as_list(v):
    if v is None:
        return []

    if isinstance(v, (list, tuple, set)):
        return [u(x).strip() for x in v if u(x).strip()]

    if isinstance(v, (str, unicode)):
        s = u(v).strip()
        if not s:
            return []
        js = json_loads_safe(s)
        if isinstance(js, list):
            return [u(x).strip() for x in js if u(x).strip()]
        parts = [p.strip() for p in _SPLIT_RE.split(s) if p.strip()]
        return [u(p) for p in parts]

    s = u(v).strip()
    return [s] if s else []


def get_selected_targets_from_conditions(conditions, title=u"染色靶标"):
    title = u(title).strip()
    if not conditions:
        return []

    out = []
    for cond in conditions:
        try:
            ctitle = u((cond or {}).get("title", "")).strip()
            if ctitle != title:
                continue
            out.extend(_as_list((cond or {}).get("value")))
        except Exception:
            continue

    # 去重保序
    seen = set()
    uniq = []
    for x in out:
        k = u(x).strip()
        if not k or k in seen:
            continue
        uniq.append(k)
        seen.add(k)
    return uniq


def _normalize_target_token(token):
    s = u(token).strip()
    if not s:
        return u""
    s = re.sub(u"[\u00A0\s]+", u"_", s)
    s = re.sub(u"[-/\\\\]+", u"_", s)
    s = re.sub(u"_+", u"_", s)
    return s.strip(u"_")


def _build_allowed_result_keywords(targets, prefix=u"result_"):
    prefix = u(prefix)
    allowed = set()

    for t in (targets or []):
        raw = u(t).strip()
        if not raw:
            continue
        norm = _normalize_target_token(raw)

        candidates = set([raw, norm, raw.lower(), raw.upper(), norm.lower(), norm.upper()])
        for c in candidates:
            c = u(c).strip()
            if c:
                allowed.add(prefix + c)

    return allowed


def _has_special_controls(interim_fields):
    for f in (interim_fields or []):
        kw = u((f or {}).get("keyword", "")).strip()
        rt = _get_result_type(f)
        if kw.lower().startswith(u"result_") and rt in SPECIAL_RESULT_TYPES:
            return True
    return False


def filter_interim_fields_by_targets(interim_fields, targets, debug=False):
    interim_fields = interim_fields or []
    targets = targets or []

    if not interim_fields:
        return interim_fields
    if not _has_special_controls(interim_fields):
        if debug:
            logger.info("[targets-filter] skip: no special result_type fields found")
        return interim_fields

    # 没有选择conditions对应的字段
    if not targets:
        out = []
        hidden = []
        for f in interim_fields:
            kw = u((f or {}).get("keyword", "")).strip()
            rt = _get_result_type(f)
            if kw.lower().startswith(u"result_") and rt in SPECIAL_RESULT_TYPES:
                hidden.append(kw)
                continue
            out.append(f)
        if debug and hidden:
            logger.info("[targets-filter] no targets -> hide=%r", hidden)
        return out

    # 选择相应的conditions字段
    allowed = _build_allowed_result_keywords(targets, prefix=u"result_")
    if debug:
        logger.info("[targets-filter] targets=%r allowed(sample)=%r",
                    targets, list(sorted(allowed))[:10])

    out = []
    dropped = []
    for f in interim_fields:
        kw = u((f or {}).get("keyword", "")).strip()
        rt = _get_result_type(f)

        if kw.lower().startswith(u"result_") and rt in SPECIAL_RESULT_TYPES:
            if kw in allowed:
                out.append(f)
            else:
                dropped.append(kw)
        else:
            out.append(f)

    if debug and dropped:
        logger.info("[targets-filter] dropped=%r", dropped)
    return out
