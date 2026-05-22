# -*- coding: utf-8 -*-
"""
parse_excel_util.py
解析 excel 文件，返回 columns + rows
支持单文件解析，以及 table1 + table2 按 raw_clonotype_id 合并解析
"""
import logging
import json

logger = logging.getLogger(__name__)

# table1 和 table2 的 join 关联键
_JOIN_KEY = "raw_clonotype_id"


def parse_excel_attachment(attachment):
    """
    从 SENAITE Attachment 对象解析 excel 内容。

    返回：
    {
        "columns": ["col1", "col2", ...],
        "rows": [
            {"col1": "val1", "col2": "val2", ...},
            ...
        ]
    }
    失败返回 None。
    """
    try:
        att_file = attachment.getAttachmentFile()
        if not att_file:
            logger.warning("[parse_excel] no AttachmentFile")
            return None

        # 读取文件二进制内容
        data = att_file.data
        if hasattr(data, "data"):
            data = data.data

        if not data:
            logger.warning("[parse_excel] empty file data")
            return None

        # 用 openpyxl 解析
        import io
        try:
            import openpyxl
        except ImportError:
            logger.error("[parse_excel] openpyxl not installed")
            return None

        wb = openpyxl.load_workbook(
            io.BytesIO(data),
            read_only=True,
            data_only=True
        )
        ws = wb.active

        rows_iter = list(ws.iter_rows(values_only=True))
        if not rows_iter:
            logger.warning("[parse_excel] empty worksheet")
            return None

        # 第一行作为列名
        raw_headers = rows_iter[0]
        columns = []
        for h in raw_headers:
            col = u""
            if h is not None:
                try:
                    col = unicode(h).strip()
                except Exception:
                    col = str(h).strip().decode("utf-8", errors="ignore")
            columns.append(col or u"")

        # 其余行作为数据
        rows = []
        for raw_row in rows_iter[1:]:
            row_dict = {}
            all_empty = True
            for i, val in enumerate(raw_row):
                col_name = columns[i] if i < len(columns) else u"col_%d" % i
                cell_val = u""
                if val is not None:
                    try:
                        cell_val = unicode(val).strip()
                    except Exception:
                        cell_val = str(val).strip().decode("utf-8", errors="ignore")
                    if cell_val:
                        all_empty = False
                row_dict[col_name] = cell_val

            # 跳过全空行
            if all_empty:
                continue

            # 默认未选中，优先级为空
            row_dict["__checked__"] = False
            row_dict["__priority__"] = u""
            rows.append(row_dict)

        wb.close()
        return {
            "columns": columns,
            "rows": rows,
        }

    except Exception as e:
        logger.exception("[parse_excel] failed: %s", e)
        return None

def parse_and_merge_excel_attachments(attachments):
    """
    解析两个 attachment 并按 raw_clonotype_id 合并。
    attachments[0] → table1（主表，含 tcr_frequency / tcr_cdr3s_aa 等汇总信息）
    attachments[1] → table2（副表，含 tra/trb 详细序列信息）
    合并规则：
    - 以 table1 为主（left join）
    - 按 raw_clonotype_id 关联
    - table2 中除 raw_clonotype_id 之外的列追加到 table1 列后面
    - table1 中找不到对应 table2 记录的行，table2 列填空字符串
    只有一个文件时退化为单文件解析。
    返回合并后的 {"columns": [...], "rows": [...]}，失败返回 None。
    """
    if not attachments:
        return None

    # 只有一个文件，走原有单文件逻辑
    if len(attachments) == 1:
        return parse_excel_attachment(attachments[0])
    parsed1 = parse_excel_attachment(attachments[0])
    parsed2 = parse_excel_attachment(attachments[1])

    if not parsed1:
        logger.warning("[parse_excel] table1 parse failed, fallback to table2 only")
        return parsed2
    if not parsed2:
        logger.warning("[parse_excel] table2 parse failed, fallback to table1 only")
        return parsed1

    # 建 table2 的索引：raw_clonotype_id -> row
    table2_index = {}
    for row in parsed2["rows"]:
        key = (row.get(_JOIN_KEY) or u"").strip()
        if key:
            table2_index[key] = row

    extra_cols = [c for c in parsed2["columns"] if c != _JOIN_KEY]
    merged_columns = list(parsed1["columns"]) + extra_cols
    merged_rows = []
    for row in parsed1["rows"]:
        new_row = dict(row)
        key = (row.get(_JOIN_KEY) or u"").strip()
        t2_row = table2_index.get(key, {})
        for col in extra_cols:
            new_row[col] = t2_row.get(col, u"")
        merged_rows.append(new_row)

    return {
        "columns": merged_columns,
        "rows": merged_rows,
    }

def tcr_data_to_json(parsed):
    """把解析结果转成 JSON 字符串存入 InterimField。"""
    if not parsed:
        return u""
    try:
        return unicode(json.dumps(parsed, ensure_ascii=False))
    except Exception:
        return u""

def tcr_data_from_json(raw):
    """从 InterimField 的 JSON 字符串还原解析结果。"""
    if not raw:
        return None
    try:
        if isinstance(raw, dict):
            return raw
        result = json.loads(raw)
        if isinstance(result, dict) and "columns" in result:
            return result
        return None
    except Exception:
        return None