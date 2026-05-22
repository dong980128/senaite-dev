# -*- coding: utf-8 -*-
from __future__ import print_function

from bika.lims import api
from Products.Five import BrowserView
from Products.CMFCore.utils import getToolByName
from zope.component.hooks import getSite

from senaite.core.mysql.importer import (
    list_tables_for,
    get_analysis_keywords,
    samples_by_keyword,
    resolve_handler,
)


class _NoopLogger(object):
    def debug(self, *a, **k):   pass

    def info(self, *a, **k):    pass

    def warning(self, *a, **k): pass

    warn = warning

    def error(self, *a, **k):   pass

    def exception(self, *a, **k): pass

    def critical(self, *a, **k): pass


logger = _NoopLogger()


def _get(form, key, default=None):
    val = form.get(key)
    return val if (val is not None and val != "") else default


def _from_obj_or_dict(obj, name, default=u""):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    if hasattr(obj, name):
        val = getattr(obj, name)
        try:
            return val() if callable(val) else val
        except Exception:
            return default
    return default


def _brain_state(brain_or_obj):
    try:
        v = getattr(brain_or_obj, "review_state", None)
        if v:
            return v
        v = getattr(brain_or_obj, "getReviewState", None)
        if callable(v):
            return v()
        v = getattr(brain_or_obj, "state", None)
        if v:
            return v
        if hasattr(brain_or_obj, "getReviewState"):
            try:
                return brain_or_obj.getReviewState()
            except Exception:
                pass
        return u""
    except Exception:
        return u""


def _safe_sample_id(item):
    for key in ("getRequestID", "getId"):
        v = _from_obj_or_dict(item, key, None)
        if v:
            return v
    # dict 的常见字段
    if isinstance(item, dict):
        v = item.get("sample_id") or item.get("request_id")
        if v:
            return v
        return item.get("id") or item.get("UID") or u""
    # 对象/brain 试属性
    v = getattr(item, "id", None)
    if v:
        return v
    v = getattr(item, "UID", None)
    if v:
        return v
    return u""


def _ensure_unicode(v):
    if v is None:
        return u""
    # 已经是 unicode
    if isinstance(v, unicode):
        return v
    try:
        return unicode(v, "utf-8", errors="ignore")
    except Exception:
        try:
            return unicode(str(v), "utf-8", errors="ignore")
        except Exception:
            return u""


def _uargs(*args):
    """把所有参数转成 unicode，供 logger 使用"""
    return tuple(_ensure_unicode(a) for a in args)


def _ulog_info(msg, *args):
    # msg 必须是 unicode
    logger.info(_ensure_unicode(msg), *_uargs(*args))


def _ulog_warn(msg, *args):
    logger.warn(_ensure_unicode(msg), *_uargs(*args))


def _ulog_error(msg, *args):
    logger.error(_ensure_unicode(msg), *_uargs(*args))


def _urepr(obj):
    try:
        import json
        return _ensure_unicode(json.dumps(obj, ensure_ascii=False))
    except Exception:
        return _ensure_unicode(repr(obj))


def _safe_text(item, *names):
    for n in names:
        v = _from_obj_or_dict(item, n, None)
        if v is None:
            continue
        v = _ensure_unicode(v).strip()
        if v:
            return v
    return u""


def dump_analysis_interims(obj, sample_id, subject_uid=u"", review_state=u"", title=u""):
    sid = _ensure_unicode(sample_id)
    subj = _ensure_unicode(subject_uid)
    st = _ensure_unicode(review_state)
    ti = _ensure_unicode(title)

    fields = _iter_interims(obj)
    if not fields and getattr(obj, "aq_parent", None) is not None:
        fields = _iter_interims(obj.aq_parent)

    header = u"=== Sample[{sid}] SubjectUID[{subj}] State[{st}] Title[{ti}] ===".format(
        sid=sid or u"-", subj=subj or u"-", st=st or u"-", ti=ti or u"-"
    )
    _ulog_info(header)

    if not fields:
        _ulog_info(u"  (no interim fields)")
        return

    for f in fields:
        kw = _ensure_unicode(f.get("keyword"))
        val = _ensure_unicode(f.get("value"))
        lab = _ensure_unicode(f.get("title") or f.get("label") or kw)
        _ulog_info(u"  - %s [%s] = %s", lab, kw, val)


def _format_interims_for_display(obj, max_items=20, max_each_len=128):
    def _truncate(u, n):
        u = _ensure_unicode(u or u"")
        return u if len(u) <= n else (u[:n] + u"...")

    fields = _iter_interims(obj)
    if not fields and getattr(obj, "aq_parent", None) is not None:
        fields = _iter_interims(obj.aq_parent)
    if not fields:
        return u""

    items_html = []
    count = 0
    for f in fields:
        kw = _ensure_unicode(f.get("keyword") or u"")
        val = _ensure_unicode(f.get("value") or u"").strip()
        if not val:
            continue
        lab = _ensure_unicode(f.get("title") or f.get("label") or kw or u"-").strip()
        # 适当截断，避免过长
        text = u"%s：%s" % (_truncate(lab, 60), _truncate(val, max_each_len))
        items_html.append(u'<div class="interim-item" title="%s">%s</div>' % (
            _ensure_unicode(text), _ensure_unicode(text)
        ))
        count += 1
        if count >= max_items:
            break

    if not items_html:
        return u""

    return u'''
    <div class="interims-wrap">
      <div class="interims-box">%s</div>
      <button type="button" class="interims-toggle" aria-expanded="false">展开</button>
    </div>
    ''' % u"".join(items_html)


def _iter_interims(obj):
    get_if = getattr(obj, "getInterimFields", None)
    if callable(get_if):
        try:
            data = get_if() or []
            return data if isinstance(data, (list, tuple)) else []
        except Exception as ex:
            logger.exception("getInterimFields() failed on %r: %r", obj, ex)
    return []


def _has_meaningful_interims(obj):
    """返回 True 表示该 Analysis/AR 上存在至少一个“有值”的 interim 字段。"""
    try:
        fields = _iter_interims(obj)
        if not fields and getattr(obj, "aq_parent", None) is not None:
            fields = _iter_interims(obj.aq_parent)
        for f in fields or []:
            v = f.get("value")
            if v is None:
                continue
            if isinstance(v, basestring):
                if v.strip():
                    return True
            else:
                return True
        return False
    except Exception:
        return False


def _pick_latest_analysis(brains, prefer_keyword=None):
    if not brains:
        return None

    BAD_STATES = set(["retracted", "invalid", "cancelled", "rejected", "invalidated", "retracted_state"])

    def _kw_ok(b):
        if not prefer_keyword:
            return True
        try:
            return getattr(b, "getKeyword", "") == prefer_keyword or \
                getattr(b, "Keyword", "") == prefer_keyword
        except Exception:
            return False

    def _scan(filter_kw=False, require_valid_state=False, require_values=False):
        for b in brains:
            if filter_kw and not _kw_ok(b):
                continue
            st = getattr(b, "review_state", "") or getattr(b, "getReviewState", lambda: "")()
            if require_valid_state and st in BAD_STATES:
                continue
            if require_values:
                try:
                    obj = b.getObject()
                    if not _has_meaningful_interims(obj):
                        continue
                except Exception:
                    continue
            return b
        return None

    picked = _scan(filter_kw=True, require_valid_state=True, require_values=True)
    if picked:
        return picked

    picked = _scan(filter_kw=True, require_valid_state=True, require_values=False)
    if picked:
        return picked

    picked = _scan(filter_kw=False, require_valid_state=True, require_values=True)
    if picked:
        return picked

    picked = _scan(filter_kw=False, require_valid_state=True, require_values=False)
    if picked:
        return picked

    return brains[0]


def _resolve_analysis_obj(item, keyword=None):
    try:
        if hasattr(item, "getObject"):
            return item.getObject()

        site = getSite()
        pc = getToolByName(site, "portal_catalog", None)

        if isinstance(item, dict):
            obj = item.get("obj") or item.get("object")
            if obj is not None:
                return obj

            uid = item.get("UID") or item.get("uid") or item.get("uid_catalog")
            if uid and pc is not None:
                brains = pc(UID=uid)
                if brains:
                    return brains[0].getObject()

            ar_path = item.get("ar_path") or item.get("path")
            if ar_path and pc is not None:
                query = dict(
                    path={"query": ar_path, "depth": 3},
                    portal_type="Analysis",
                    sort_on="modified",
                    sort_order="descending",
                )
                if keyword:
                    # 有些站点索引名是 getKeyword
                    query["getKeyword"] = keyword
                brains = pc(**query)

                # 若按 keyword 没命中，放宽只按路径
                if not brains:
                    brains = pc(path={"query": ar_path, "depth": 3},
                                portal_type="Analysis",
                                sort_on="modified",
                                sort_order="descending")

                if brains:
                    picked = _pick_latest_analysis(brains, prefer_keyword=keyword)
                    if picked:
                        return picked.getObject()

            if ar_path:
                try:
                    ar = site.unrestrictedTraverse(ar_path.lstrip("/"))
                except Exception:
                    ar = None
                if ar is not None:
                    children = [c for c in getattr(ar, "objectValues", lambda *a, **k: [])("Analysis")]
                    if children:
                        # 先挑 kw + 有效 + 有值
                        BAD = set(["retracted", "invalid", "cancelled", "rejected", "invalidated", "retracted_state"])

                        def _ok_state(o):
                            st = getattr(o, "review_state", "") or getattr(o, "getReviewState", lambda: "")()
                            return st not in BAD

                        # 排序：按修改时间倒序、再按 id 后缀数字倒序
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

                        if keyword:
                            for c in children:
                                try:
                                    svc_getter = getattr(c, "getAnalysisService", None)
                                    service = svc_getter() if callable(svc_getter) else getattr(c, "getService",
                                                                                                lambda: None)()
                                    kw = getattr(service, "getKeyword", lambda: None)() or ""
                                except Exception:
                                    kw = None
                                if kw == keyword and _ok_state(c) and _has_meaningful_interims(c):
                                    return c

                        for c in children:
                            if _ok_state(c) and _has_meaningful_interims(c):
                                return c

                        for c in children:
                            if _ok_state(c):
                                return c

                        return children[0]

        return None
    except Exception as ex:
        logger.exception("Resolve analysis obj failed for %r: %r", item, ex)
        return None


def _collect_columns(rows, prefer_order=("sample_id", "subject_uid")):
    cols = []
    seen = set()
    # 先把优先列放进去
    for c in prefer_order:
        seen.add(c);
        cols.append(c)
    # 再合并所有行的键
    for r in rows or []:
        for k in r.keys():
            if k not in seen:
                seen.add(k);
                cols.append(k)
    return cols


def _handle_submit(self):
    # 取表单
    req = api.get_request()
    experiment = req.form.get("experiment")
    table = req.form.get("table")
    limit_raw = req.form.get("limit")
    review_state = (req.form.get("review_state") or u"").strip()  # 允许逗号分隔
    is_dry_run = req.form.get("dry_run") == "on"  # 预览(不写库)

    # 规范化 limit
    try:
        limit = int(limit_raw) if (limit_raw not in (None, u"", "") and str(limit_raw).isdigit()) else None
    except Exception:
        limit = None

    # 统一参数传给处理器
    params = {
        "keyword": experiment,  # 我们的 samples_by_keyword 用实验关键字查询
        "limit": limit,
        "review_state": review_state,
        "table": table,
    }

    # 解析处理器
    try:
        handler = resolve_handler(experiment, table)
    except LookupError as exc:
        self.context.plone_utils.addPortalMessage(unicode(exc), type="error")
        self.result = None
        return

    # 执行：预览 or 真导入
    if is_dry_run:
        try:
            data = handler.preview(self.context, params)
        except Exception as e:
            self.context.plone_utils.addPortalMessage(u"预览失败：%s" % unicode(e), type="error")
            self.result = None
            return

        # 供模板渲染
        self.preview_columns = data.get("columns", []) or []
        self.preview_rows = data.get("rows", []) or []
        self.preview = self.preview_rows
        self.preview_meta = {
            "handled": data.get("handled", 0),
            "dry_run": True,
            "handler": handler.describe(),
        }
        self.result = {
            "handled": data.get("handled", 0),
            "inserted": 0,
            "dry_run": True,
        }
        self.context.plone_utils.addPortalMessage(
            u"这是预览（未写库），确认无误后点击“执行导入”。", type="info"
        )
    else:
        try:
            data = handler.execute(self.context, params)
        except Exception as e:
            self.context.plone_utils.addPortalMessage(u"执行导入失败：%s" % unicode(e), type="error")
            self.result = None
            return

        handled = int(data.get("handled", 0))
        inserted = int(data.get("inserted", 0))
        skipped = int(data.get("skipped", 0)) if "skipped" in data else 0

        # 提示信息：带上 skipped（如果有）
        if skipped:
            msg = u"导入完成：处理 {handled} 条，成功写入 {inserted} 条，跳过 {skipped} 条（表：{table}）。"
        else:
            msg = u"导入完成：处理 {handled} 条，成功写入 {inserted} 条（表：{table}）。"
        self.context.plone_utils.addPortalMessage(
            msg.format(handled=handled, inserted=inserted, skipped=skipped, table=table),
            type="info",
        )

        self.result = {
            "handled": handled,
            "inserted": inserted,
            "skipped": skipped,
            "dry_run": False,
        }

        # 清空预览（避免旧数据残留影响 PT 判断）
        self.preview_columns = []
        self.preview_rows = []
        self.preview_meta = {}


class MysqlImporterView(BrowserView):

    def _as_bool(self, val, default=False):
        if val is None:
            return default
        s = unicode(val).strip().lower()
        if s in ("1", "true", "on", "yes"):
            return True
        if s in ("0", "false", "off", "no"):
            return False
        return default

    def update(self):
        req = self.request
        form = req.form

        # 页面状态（模板会访问的属性，先赋默认值防止AttributeError）
        self.message = None
        self.error = None
        self.result = None
        self.preview = []
        self.preview_columns = []
        self.samples = []

        # 下拉选项与当前选择
        site = getSite()
        self.experiments = get_analysis_keywords(site)  # 自动从 ZODB 收集 getKeyword
        self.selected_experiment = _get(
            form, "experiment", self.experiments[0] if self.experiments else u""
        )

        self.tables = list_tables_for(self.selected_experiment) if self.selected_experiment else []
        self.selected_table = _get(form, "table", self.tables[0] if self.tables else u"")

        # 查询/导入参数
        try:
            self.limit = int(_get(form, "limit", 100))
        except Exception:
            self.limit = 100

        # 预览(不写库)
        self.dry_run = self._as_bool(_get(form, "dry_run", "on"), default=True)

        self.center_name = _get(form, "center_name", u"")
        self.source = _get(form, "source", u"")
        self.disease_diagnosis = _get(form, "disease_diagnosis", u"")

        # 状态过滤（逗号分隔）
        self.review_state = (form.get('review_state') or u"").strip()

        # 提交流程
        self.peek_samples = form.get("peek_samples") == "1"  # 查看样本
        self.did_submit = form.get("submit") == "1"  # 预览/执行导入

        # 仅查看样本：根据注册的样本来源返回列表（并打印对应实验结果到日志）
        if self.peek_samples and self.selected_experiment:
            params = dict(
                limit=self.limit,
                center_name=self.center_name,
                source=self.source,
                disease_diagnosis=self.disease_diagnosis,
                review_state=getattr(self, "review_state", u""),
            )
            try:
                items = samples_by_keyword(getSite(), self.selected_experiment, params)
                logger.info("Peek samples (exp=%s): %s items", self.selected_experiment, len(items))

                rows = []
                for it in items:
                    # 不要假设 it 是 brain；它可能是 dict
                    sample_id = _safe_sample_id(it)
                    review_state = _safe_text(it, "review_state", "reviewState", "state")
                    title = _safe_text(it, "Title", "title")
                    client_title = _safe_text(it, "getClientTitle", "ClientTitle", "client")

                    # 取 SubjectUID
                    subject_uid = _safe_text(it, "SubjectUID", "subject_uid")
                    obj = _resolve_analysis_obj(it, keyword=self.selected_experiment)
                    if not subject_uid and obj is not None:
                        subject_uid = getattr(obj, "SubjectUID", u"") or (
                            getattr(getattr(obj, "aq_parent", None), "SubjectUID", u"")
                            if getattr(obj, "aq_parent", None) else u""
                        )

                    display_html = u""
                    if obj is not None:
                        dump_analysis_interims(obj,
                                               sample_id=sample_id,
                                               subject_uid=subject_uid,
                                               review_state=review_state,
                                               title=title)
                        display_html = _format_interims_for_display(obj)
                    else:
                        logger.warn("Cannot resolve Analysis object for item=%r (sample_id=%r)", it, sample_id)

                    rows.append({
                        "sample_id": sample_id,
                        "client": client_title,
                        "review_state": review_state,
                        "subject_uid": subject_uid,
                        "result_data": display_html,
                    })

                self.samples = rows
                if not rows:
                    self.message = u"未找到该实验的样本。"

            except Exception as ex:
                logger.exception("获取样本失败: %r", ex)
                self.error = u"获取样本失败：%r" % ex

            return

        # —— 预览/执行导入（新流程） —— #
        if self.did_submit and self.selected_experiment and self.selected_table:
            _handle_submit(self)
            return

            params = dict(
                dry_run=self.dry_run,
                limit=self.limit,
                center_name=self.center_name,
                source=self.source,
                disease_diagnosis=self.disease_diagnosis,
                review_state=self.review_state,
                experiment=self.selected_experiment,
            )

            try:
                res = handler(site, params)
                self.result = res
                self.preview = res.get("preview") or []
                self.preview_columns = res.get("columns") or _collect_columns(self.preview)
                if res.get("error"):
                    self.error = u"导入出现错误：%s" % res["error"]
                else:
                    if self.dry_run:
                        self.message = u"这是预览（未写库）。确认无误后点“执行导入”。"
                    else:
                        self.message = u"导入完成：处理 %s 条，成功写入 %s 条。" % (
                            res.get("handled", 0), res.get("inserted", 0)
                        )
            except Exception as ex:
                self.error = u"执行异常：%r" % ex
            return

    def __call__(self):
        if self.request.method == "POST":
            self.update()
            return self.index()
        self.update()
        return self.index()
