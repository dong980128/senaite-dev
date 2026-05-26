# -*- coding: utf-8 -*-
"""
upload_analysis_file.py
@@upload-analysis-file
绑定在 AR/Sample 上，处理 InterimField 文件上传。

POST 参数：
    analysis_uid   — Analysis 对象的 UID
    field_keyword  — InterimField 的 keyword
    file_upload    — 文件（multipart/form-data）
    _authenticator — CSRF token

返回 JSON：
    {
        "success": true,
        "uid": "附件UID",
        "filename": "文件名",
        "download_url": "下载地址",
        "field_keyword": "字段keyword"
    }
"""

import json
import logging

from bika.lims import api
from mimetypes import guess_type
from Products.Five.browser import BrowserView
from senaite.core.labprocess.utils_auth import require_post
from senaite.core.labprocess.utils_common import get_uid, to_unicode
from zExceptions import BadRequest

logger = logging.getLogger(__name__)


def _get_attachment_filename(att):
    """从 Attachment 对象获取真实文件名。"""
    try:
        f = att.getAttachmentFile()
        if f and getattr(f, "filename", None):
            name = to_unicode(f.filename)
            # 有些环境 filename 带路径，只取最后一段
            return name.replace("\\", "/").split("/")[-1]
    except Exception:
        pass
    # 兜底：用 Title
    return to_unicode(att.Title() or "")


class UploadAnalysisFileView(BrowserView):

    def __call__(self):
        try:
            require_post(self.request)
        except BadRequest as e:
            return self._json_error(str(e))

        form = self.request.form
        analysis_uid = to_unicode(form.get("analysis_uid", "") or "").strip()
        field_keyword = to_unicode(form.get("field_keyword", "") or "").strip()
        upload = form.get("file_upload", None)

        if not analysis_uid:
            return self._json_error("analysis_uid is required")
        if not field_keyword:
            return self._json_error("field_keyword is required")
        if upload is None or not getattr(upload, "filename", None):
            return self._json_error("file_upload is required")

        analysis = api.get_object_by_uid(analysis_uid, default=None)
        if analysis is None:
            return self._json_error("Analysis not found: %s" % analysis_uid)

        ar = getattr(analysis, "aq_parent", None)
        if ar is None:
            return self._json_error("Cannot find parent AR")

        client = None
        for getter in ("getClient", "aq_parent"):
            if callable(getattr(ar, getter, None)):
                try:
                    client = getattr(ar, getter)()
                    break
                except Exception:
                    pass
            elif getter == "aq_parent":
                client = getattr(ar, "aq_parent", None)
                break

        if client is None:
            return self._json_error("Cannot find client container")

        try:
            attachment = self._create_attachment(client, upload)
        except Exception as e:
            logger.exception("[upload_analysis_file] create_attachment failed")
            return self._json_error("Failed to create attachment: %s" % str(e))

        att_uid = get_uid(attachment)
        filename = to_unicode(getattr(upload, "filename", "") or "")
        download_url = attachment.absolute_url() + "/AttachmentFile"

        try:
            self._set_interim_value(analysis, field_keyword, att_uid)
        except Exception as e:
            logger.exception("[upload_analysis_file] set_interim_value failed")
            return self._json_error("Failed to update InterimField: %s" % str(e))

        try:
            self._link_attachment_to_analysis(analysis, attachment)
        except Exception as e:
            logger.warning("[upload_analysis_file] link attachment failed: %s", e)

        self.request.response.setHeader("Content-Type", "application/json")
        return json.dumps({
            "success": True,
            "uid": att_uid,
            "filename": filename,
            "download_url": download_url,
            "field_keyword": field_keyword,
        })

    def _create_attachment(self, container, upload):
        """
        在 container（client）下创建 Attachment 对象。
        完全复用 SENAITE AttachmentsView.create_attachment 的逻辑。
        """
        filename = to_unicode(getattr(upload, "filename", "") or u"Attachment")

        # 创建 Attachment 内容对象
        attachment = api.create(container, "Attachment", title=filename)

        # 写入文件（会自动识别 MIME 类型）
        attachment.edit(AttachmentFile=upload)
        attachment.processForm()
        attachment.reindexObject()
        return attachment

    def _set_interim_value(self, analysis, keyword, value):
        """
        把 value（Attachment UID）写入 Analysis 的指定 InterimField。
        同时更新 attachments 列表，供前端展示所有已关联文件。
        """
        interims = analysis.getInterimFields() or []
        found = False

        # 构建当前 analysis 上所有附件的 {filename, download_url} 列表
        all_attachments = []
        for att in (analysis.getAttachment() or []):
            att_uid = get_uid(att)
            all_attachments.append({
                "uid": att_uid,
                "filename": _get_attachment_filename(att),
                "download_url": att.absolute_url() + "/AttachmentFile",
                "success": True,
            })

        for interim in interims:
            if interim.get("keyword") == keyword:
                interim["value"] = to_unicode(value)
                interim["attachments"] = all_attachments
                # 取第一个附件作为 filename/download_url（兼容旧逻辑）
                if all_attachments:
                    interim["filename"] = all_attachments[0]["filename"]
                    interim["download_url"] = all_attachments[0]["download_url"]
                found = True
                break

        if not found:
            # InterimField 不存在，动态追加
            logger.warning(
                "[upload_analysis_file] keyword=%s not found in interims, appending",
                keyword
            )
            entry = {
                "keyword": keyword,
                "title": keyword,
                "value": to_unicode(value),
                "result_type": "file",
                "attachments": all_attachments,
            }
            if all_attachments:
                entry["filename"] = all_attachments[0]["filename"]
                entry["download_url"] = all_attachments[0]["download_url"]
            interims.append(entry)

        analysis.setInterimFields(interims)
        analysis.reindexObject()

    def _remove_existing_attachments(self, analysis):
        """
        删除该 Analysis 上已关联的所有 Attachment 对象。
        用于重新获取时先清空旧附件。
        只删除没有被其他 Analysis/AR 引用的附件，避免误删共享附件。
        """
        existing = analysis.getAttachment() or []
        if not existing:
            return

        # 先把 analysis 上的附件关联清空
        analysis.setAttachment([])
        analysis.reindexObject()

        # 逐个检查并删除没有其他引用的附件对象
        for att in existing:
            try:
                att_uid = get_uid(att)
                # 检查是否还被其他对象引用（通过 back references）
                back_refs = att.getBRefs() if hasattr(att, "getBRefs") else []
                if back_refs:
                    logger.info(
                        "[remove_existing_attachments] skip att %s, still referenced by %d objects",
                        att_uid, len(back_refs)
                    )
                    continue
                container = att.aq_parent
                container.manage_delObjects([att.getId()])
                logger.info("[remove_existing_attachments] deleted attachment %s", att_uid)
            except Exception as e:
                logger.warning(
                    "[remove_existing_attachments] failed to delete att %s: %s",
                    get_uid(att), e
                )

    def _link_attachment_to_analysis(self, analysis, attachment):
        """
        把 Attachment 关联到 Analysis（通过 setAttachment）。
        这样附件也会出现在 SENAITE 原生的附件列表里。
        """
        att_uid = get_uid(attachment)
        existing = analysis.getAttachment() or []
        existing_uids = [get_uid(a) for a in existing]

        if att_uid not in existing_uids:
            existing_uids.append(att_uid)
            analysis.setAttachment(existing_uids)
            analysis.reindexObject()

    def _json_error(self, message):
        """返回 JSON 格式的错误信息。"""
        self.request.response.setHeader("Content-Type", "application/json")
        self.request.response.setStatus(400)
        logger.error("[upload_analysis_file] error: %s", message)
        return json.dumps({
            "success": False,
            "error": message,
        })


class ListAnalysisAttachmentsView(BrowserView):
    """@@list-analysis-attachments
    GET: analysis_uid
    返回该 Analysis 上所有已关联附件的列表。
    {
        "success": true,
        "attachments": [
            {"uid": "...", "filename": "...", "download_url": "...", "success": true},
            ...
        ]
    }
    """

    def __call__(self):
        self.request.response.setHeader("Content-Type", "application/json")

        analysis_uid = to_unicode(
            self.request.get("analysis_uid", "") or ""
        ).strip()

        if not analysis_uid:
            return self._json_error("analysis_uid is required")

        analysis = api.get_object_by_uid(analysis_uid, default=None)
        if analysis is None:
            return self._json_error("Analysis not found: %s" % analysis_uid)

        attachments = []
        for att in (analysis.getAttachment() or []):
            attachments.append({
                "uid": get_uid(att),
                "filename": _get_attachment_filename(att),
                "download_url": att.absolute_url() + "/AttachmentFile",
                "success": True,
            })

        return json.dumps({
            "success": True,
            "attachments": attachments,
        })

    def _json_error(self, message):
        self.request.response.setHeader("Content-Type", "application/json")
        self.request.response.setStatus(400)
        return json.dumps({"success": False, "error": message})