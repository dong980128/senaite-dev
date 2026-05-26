# -*- coding: utf-8 -*-
"""
remote_file.py

@@list-remote-files — GET  返回远程目录文件列表（JSON）
@@fetch-remote-analysis-file — POST 从远程拉取单个文件并创建 Attachment
@@fetch-all-remote-files — POST 批量拉取目录下所有文件，自动分配到各 file InterimField
"""
import io
import json
import logging
import mimetypes
import stat

from bika.lims import api
from senaite.core.labprocess.browser.upload_analysis_file import UploadAnalysisFileView
from senaite.core.labprocess.utils_auth import require_post
from senaite.core.labprocess.utils_common import get_uid, to_unicode

try:
    from plone.app.blob.interfaces import IBlobbable
    from zope.component import provideAdapter
    from zope.interface import implementer

    _HAS_BLOB = True
except ImportError:
    _HAS_BLOB = False

logger = logging.getLogger(__name__)

# 远程服务器配置（开发测试，写死）
REMOTE_CONFIG = {
    "host": "192.168.3.114",
    "port": 22,
    "username": "ksh",
    "password": "kshadmin_123",
    "base_path": "/home/ksh/kshyun/tcrxfinder",
}


def _sftp_connect():
    """建立 SFTP 连接，返回 (ssh_client, sftp)，调用方负责关闭。"""
    import paramiko
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        REMOTE_CONFIG["host"],
        port=REMOTE_CONFIG["port"],
        username=REMOTE_CONFIG["username"],
        password=REMOTE_CONFIG["password"],
        timeout=10,
    )
    sftp = client.open_sftp()
    return client, sftp


class _RemoteFileUpload(object):
    """把 bytes + filename 包装成 file-like 对象，供 AT setAttachmentFile 使用。

    同时通过 _BlobbableRemoteFile adapter 实现 IBlobbable，让 plone.app.blob
    的 BlobField 能把数据写入 ZODB blob 存储。
    """

    def __init__(self, data, filename):
        self._data = data
        self._buf = io.BytesIO(data)
        self._filename = filename
        self.filename = filename  # AT 通过属性访问文件名
        ctype, _ = mimetypes.guess_type(filename)
        self._ctype = ctype or "application/octet-stream"
        self.headers = {"content-type": self._ctype}

    def read(self, size=-1):
        return self._buf.read() if size == -1 else self._buf.read(size)

    def seek(self, pos, *args):
        self._buf.seek(pos, *args)

    def tell(self):
        return self._buf.tell()

    def __len__(self):
        return len(self._data)


if _HAS_BLOB:
    @implementer(IBlobbable)
    class _BlobbableRemoteFile(object):
        """IBlobbable adapter: 让 plone.app.blob BlobField 能处理 _RemoteFileUpload。

        plone.app.blob 的 BlobField.set() 调用 IBlobbable(value) 查找 adapter，
        若找不到则抛出 'Could not adapt' TypeError。此处手动注册解决该问题。
        """

        def __init__(self, context):
            self._ctx = context

        def feed(self, blob):
            fp = blob.open("w")
            fp.write(self._ctx._data)
            fp.close()

        def filename(self):
            return self._ctx._filename

        def mimetype(self):
            return self._ctx._ctype


    provideAdapter(_BlobbableRemoteFile, (_RemoteFileUpload,), IBlobbable)


def _safe_name(name):
    """校验目录/文件名不含路径穿越字符。"""
    return name and "/" not in name and ".." not in name


class ListRemoteFilesView(UploadAnalysisFileView):
    """@@list-remote-files

    不传 subdir：返回 base_path 下的子目录列表（subdirs）和根文件列表（files）。
    传 subdir=NAME：返回 base_path/NAME 下的文件列表（files），subdirs 为空。
    """

    def __call__(self):
        self.request.response.setHeader("Content-Type", "application/json")

        subdir = to_unicode(self.request.get("subdir", "") or "").strip()
        if subdir and not _safe_name(subdir):
            return json.dumps({"success": False, "error": "Invalid subdir",
                               "files": [], "subdirs": []})

        base = REMOTE_CONFIG["base_path"].rstrip("/")
        target = base + "/" + subdir if subdir else base

        try:
            ssh_client, sftp = _sftp_connect()
            try:
                entries = sftp.listdir_attr(target)
                files = sorted([
                    e.filename for e in entries
                    if not e.filename.startswith(".")
                       and (e.st_mode is None or stat.S_ISREG(e.st_mode))
                ])
                subdirs = [] if subdir else sorted([
                    e.filename for e in entries
                    if not e.filename.startswith(".")
                       and e.st_mode is not None and stat.S_ISDIR(e.st_mode)
                ])
            finally:
                sftp.close()
                ssh_client.close()
        except Exception as e:
            logger.exception("[list_remote_files] SFTP error")
            return json.dumps({"success": False, "error": str(e),
                               "files": [], "subdirs": []})

        return json.dumps({"success": True, "files": files, "subdirs": subdirs})


class FetchRemoteFileView(UploadAnalysisFileView):
    """@@fetch-remote-analysis-file — 从远程服务器拉取指定文件并创建 Attachment"""

    def __call__(self):
        try:
            require_post(self.request)
        except Exception as e:
            return self._json_error(str(e))

        form = self.request.form
        analysis_uid = to_unicode(form.get("analysis_uid", "") or "").strip()
        field_keyword = to_unicode(form.get("field_keyword", "") or "").strip()
        filename = to_unicode(form.get("filename", "") or "").strip()
        subdir = to_unicode(form.get("subdir", "") or "").strip()

        if not analysis_uid:
            return self._json_error("analysis_uid is required")
        if not field_keyword:
            return self._json_error("field_keyword is required")
        if not filename:
            return self._json_error("filename is required")
        if not _safe_name(filename):
            return self._json_error("Invalid filename")
        if subdir and not _safe_name(subdir):
            return self._json_error("Invalid subdir")

        analysis = api.get_object_by_uid(analysis_uid, default=None)
        if analysis is None:
            return self._json_error("Analysis not found: %s" % analysis_uid)

        ar = getattr(analysis, "aq_parent", None)
        if ar is None:
            return self._json_error("Cannot find parent AR")

        # 找 client container（与 UploadAnalysisFileView 相同逻辑）
        container = None
        for getter in ("getClient", "aq_parent"):
            if callable(getattr(ar, getter, None)):
                try:
                    container = getattr(ar, getter)()
                    break
                except Exception:
                    pass
            elif getter == "aq_parent":
                container = getattr(ar, "aq_parent", None)
                break

        if container is None:
            return self._json_error("Cannot find client container")

        # SFTP 拉取文件到内存
        base = REMOTE_CONFIG["base_path"].rstrip("/")
        remote_path = base + "/" + subdir + "/" + filename if subdir else base + "/" + filename
        try:
            ssh_client, sftp = _sftp_connect()
            try:
                buf = io.BytesIO()
                sftp.getfo(remote_path, buf)
                file_data = buf.getvalue()
            finally:
                sftp.close()
                ssh_client.close()
        except Exception as e:
            logger.exception("[fetch_remote_file] SFTP fetch failed: %s", remote_path)
            return self._json_error("Failed to fetch remote file: %s" % str(e))

        # 复用父类方法创建 Attachment
        upload = _RemoteFileUpload(file_data, filename)
        try:
            attachment = self._create_attachment(container, upload)
        except Exception as e:
            logger.exception("[fetch_remote_file] create_attachment failed")
            return self._json_error("Failed to create attachment: %s" % str(e))

        att_uid = get_uid(attachment)
        download_url = attachment.absolute_url() + "/AttachmentFile"

        try:
            self._set_interim_value(analysis, field_keyword, att_uid)
        except Exception as e:
            logger.exception("[fetch_remote_file] set_interim_value failed")
            return self._json_error("Failed to update InterimField: %s" % str(e))

        try:
            self._link_attachment_to_analysis(analysis, attachment)
        except Exception as e:
            logger.warning("[fetch_remote_file] link attachment failed: %s", e)

        self.request.response.setHeader("Content-Type", "application/json")
        return json.dumps({
            "success": True,
            "uid": att_uid,
            "filename": filename,
            "download_url": download_url,
            "field_keyword": field_keyword,
        })


class FetchAllRemoteFilesView(UploadAnalysisFileView):
    """@@fetch-all-remote-files
    POST: analysis_uid, subdir, [field_keyword]
    把 subdir 下的全部文件都作为 Attachment 链接到 analysis。
    若提供 field_keyword，则把第一个文件的 UID 写入该 InterimField（用于前端展示）。
    返回: {success, count, field_keyword, first_uid, first_filename, first_download_url, files:[...]}
    """

    def __call__(self):
        try:
            require_post(self.request)
        except Exception as e:
            return self._json_error(str(e))

        form = self.request.form
        analysis_uid = to_unicode(form.get("analysis_uid", "") or "").strip()
        subdir = to_unicode(form.get("subdir", "") or "").strip()
        field_keyword = to_unicode(form.get("field_keyword", "") or "").strip()

        if not analysis_uid:
            return self._json_error("analysis_uid is required")
        if not subdir or not _safe_name(subdir):
            return self._json_error("subdir is required and must be valid")

        analysis = api.get_object_by_uid(analysis_uid, default=None)
        if analysis is None:
            return self._json_error("Analysis not found: %s" % analysis_uid)

        ar = getattr(analysis, "aq_parent", None)
        if ar is None:
            return self._json_error("Cannot find parent AR")

        container = None
        for getter in ("getClient", "aq_parent"):
            if callable(getattr(ar, getter, None)):
                try:
                    container = getattr(ar, getter)()
                    break
                except Exception:
                    pass
            elif getter == "aq_parent":
                container = getattr(ar, "aq_parent", None)
                break

        if container is None:
            return self._json_error("Cannot find client container")

        # # 收集该 analysis 上所有 file 类型的 InterimField
        # interims = analysis.getInterimFields() or []
        # file_fields = [f for f in interims if f.get("result_type") == "file"]
        # if not file_fields:
        #     return self._json_error("No file-type InterimFields found on this analysis")

        # 列出 subdir 下的全部文件
        base = REMOTE_CONFIG["base_path"].rstrip("/")
        target = base + "/" + subdir
        try:
            ssh_client, sftp = _sftp_connect()
            try:
                entries = sftp.listdir_attr(target)
                remote_files = sorted([
                    e.filename for e in entries
                    if not e.filename.startswith(".")
                       and (e.st_mode is None or stat.S_ISREG(e.st_mode))
                ])
            finally:
                sftp.close()
                ssh_client.close()
        except Exception as e:
            logger.exception("[fetch_all_remote_files] SFTP listdir failed: %s", target)
            return self._json_error("Failed to list remote files: %s" % str(e))

        if not remote_files:
            return self._json_error("No files found in directory: %s" % subdir)

        # 删除该 analysis 上已有的旧附件（重新获取前清理）
        self._remove_existing_attachments(analysis)

        # 按位置配对：第 i 个文件 → 第 i 个 file InterimField
        # results = []
        # for i, field in enumerate(file_fields):
        #     if i >= len(remote_files):
        #         break
        #
        #     filename = remote_files[i]
        #     field_keyword = field.get("keyword")
        #     remote_path = target + "/" + filename

        # 逐个获取并创建 Attachment, 全部挂在到 analysis
        files = []
        for filename in remote_files:
            remote_path = target + "/" + filename
            try:
                ssh_client, sftp = _sftp_connect()
                try:
                    buf = io.BytesIO()
                    sftp.getfo(remote_path, buf)
                    file_data = buf.getvalue()
                finally:
                    sftp.close()
                    ssh_client.close()
            except Exception as e:
                logger.exception("[fetch_all_remote_files] SFTP fetch failed: %s", remote_path)
                files.append({"filename": filename, "success": False,
                              "error": "Failed to fetch: %s" % str(e)})
                continue

            upload = _RemoteFileUpload(file_data, filename)
            try:
                attachment = self._create_attachment(container, upload)
            except Exception as e:
                logger.exception("[fetch_all_remote_files] create_attachment failed: %s", filename)
                # results.append({"keyword": field_keyword, "success": False,
                #                 "error": "Failed to save %s: %s" % (filename, str(e))})
                files.append({"filename": filename, "success": False,
                              "error": "Failed to save: %s" % str(e)})
                continue

            att_uid = get_uid(attachment)
            download_url = attachment.absolute_url() + "/AttachmentFile"

            try:
                #     self._set_interim_value(analysis, field_keyword, att_uid)
                # except Exception as e:
                #     logger.exception("[fetch_all_remote_files] set_interim_value failed: %s", field_keyword)
                #     results.append({"keyword": field_keyword, "success": False,
                #                     "error": "Failed to update field %s: %s" % (field_keyword, str(e))})
                #     continue
                #
                # try:
                self._link_attachment_to_analysis(analysis, attachment)
            except Exception as e:
                logger.warning("[fetch_all_remote_files] link attachment failed: %s", e)

            # results.append({
            #     "keyword": field_keyword,
            files.append({
                "filename": filename,
                "success": True,
                "uid": att_uid,
                # "filename": filename,
                "download_url": download_url,
            })

        succeeded = [f for f in files if f.get("success")]

        # 若调用方提供了 field_keyword，把第一个成功文件的 UID 写入该 InterimField
        first_uid = first_filename = first_download_url = None
        if field_keyword and succeeded:
            first = succeeded[0]
            first_uid = first["uid"]
            first_filename = first["filename"]
            first_download_url = first["download_url"]
            try:
                self._set_interim_value(analysis, field_keyword, first_uid)
            except Exception as e:
                logger.exception("[fetch_all_remote_files] set_interim_value failed: %s", field_keyword)

        self.request.response.setHeader("Content-Type", "application/json")
        return json.dumps({
            "success": True,
            "count": len(succeeded),
            "field_keyword": field_keyword,
            "first_uid": first_uid,
            "first_filename": first_filename,
            "first_download_url": first_download_url,
            "files": files,
        })