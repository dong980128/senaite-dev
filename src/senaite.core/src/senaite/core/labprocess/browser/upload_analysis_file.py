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
        """
        interims = analysis.getInterimFields() or []
        found = False

        for interim in interims:
            if interim.get("keyword") == keyword:
                interim["value"] = to_unicode(value)
                found = True
                break

        if not found:
            # InterimField 不存在，动态追加
            logger.warning(
                "[upload_analysis_file] keyword=%s not found in interims, appending",
                keyword
            )

            interims.append({
                "keyword": keyword,
                "title": keyword,
                "value": to_unicode(value),
                "result_type": "file",
            })

        analysis.setInterimFields(interims)
        analysis.reindexObject()

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
