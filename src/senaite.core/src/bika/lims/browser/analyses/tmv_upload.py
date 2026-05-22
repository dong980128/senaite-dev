# -*- coding: utf-8 -*-
from zope.publisher.browser import BrowserView
from zope.annotation.interfaces import IAnnotations
from ZODB.blob import Blob
from persistent.mapping import PersistentMapping
import json
import uuid

TMV_IMAGES_KEY = "tmv.images"


def _as_list(v):
    """Zope req.form 里 multiple file 可能是单个对象或 list"""
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return list(v)
    return [v]


def _file_info(fileobj):
    # fileobj 可能是 ZPublisher 的 FileUpload
    filename = getattr(fileobj, "filename", "") or getattr(fileobj, "name", "") or "image"
    headers = getattr(fileobj, "headers", {}) or {}
    content_type = headers.get("content-type") or headers.get("Content-Type") or "application/octet-stream"
    return filename, content_type


class TMVUploadView(BrowserView):
    """
    POST /@@tmv-upload
      - legacy: field + slot + file
      - new:    field + files(multiple) [+mode=replace|append]
    """

    def __call__(self):
        req = self.request

        field_id = req.form.get("field")

        # 兼容旧模式
        slot = req.form.get("slot")
        file_single = req.form.get("file")

        # 新模式：files 可多选
        files = _as_list(req.form.get("files"))

        # 如果啥参数都没带 → 仍然返回测试页
        if not field_id and not (slot and file_single) and not files:
            req.response.setHeader("Content-Type", "text/html; charset=utf-8")
            return u"""\
<html>
  <body>
    <h3>TMV upload test</h3>
    
    <h4>New multi upload</h4>
    <form method="post" enctype="multipart/form-data">
      <p>field: <input name="field" value="result_MAGE-A4" /></p>
      <p>mode:
        <select name="mode">
          <option value="replace">replace</option>
          <option value="append">append</option>
        </select>
      </p>
      <p>files: <input type="file" name="files" multiple /></p>
      <p><input type="submit" value="upload" /></p>
    </form>
    
    <hr/>

    <h4>Legacy single upload</h4>
    <form method="post" enctype="multipart/form-data">
      <p>field: <input name="field" value="result_MAGE-A4" /></p>
      <p>slot: <input name="slot" value="1plus" /></p>
      <p>file: <input type="file" name="file" /></p>
      <p><input type="submit" value="upload" /></p>
    </form>
  </body>
</html>
"""

        if not field_id:
            return self._json_error("missing field")

        ann = IAnnotations(self.context)
        store = ann.get(TMV_IMAGES_KEY)
        if store is None:
            store = PersistentMapping()
            ann[TMV_IMAGES_KEY] = store
        elif not isinstance(store, PersistentMapping):
            # 迁移旧数据到 PersistentMapping，保留已有数据
            new_store = PersistentMapping()
            for k, v in store.items():
                new_store[k] = v
            ann[TMV_IMAGES_KEY] = new_store
            store = new_store

        field_map = store.get(field_id)
        if field_map is None:
            field_map = PersistentMapping()
            store[field_id] = field_map
        elif not isinstance(field_map, PersistentMapping):
            # 迁移旧 field_map 到 PersistentMapping，保留已有数据
            new_field_map = PersistentMapping()
            for k, v in field_map.items():
                new_field_map[k] = v
            store[field_id] = new_field_map
            field_map = new_field_map

        # 统一准备 multi 容器（不影响 legacy slot）
        items = field_map.setdefault("__items__", {})
        order = field_map.setdefault("__order__", [])

        # ---------- legacy ----------
        if slot and file_single:
            blob = Blob()
            f = blob.open("w")
            data = file_single.read()
            f.write(data)
            f.close()

            filename, content_type = _file_info(file_single)
            # legacy 仍然放在 slot key 下
            field_map[slot] = {
                "filename": filename,
                "content_type": content_type,
                "blob": blob,
            }

            img_url = "%s/@@tmv-image?field=%s&slot=%s" % (
                self.context.absolute_url(), field_id, slot
            )
            img_rel = "@@tmv-image?field=%s&slot=%s" % (field_id, slot)

            return self._json_ok({
                "mode": "legacy",
                "url": img_url,
                "rel": img_rel,
                "filename": filename,
            })

        # ---------- new multi ----------
        mode = (req.form.get("mode") or "replace").lower().strip()
        if mode not in ("replace", "append"):
            mode = "replace"

        # 0 张图片也允许：
        # - replace：清空
        # - append：不变（直接返回当前列表）
        if not files:
            if mode == "replace":
                field_map["__items__"] = PersistentMapping()
                field_map["__order__"] = []
                items = field_map["__items__"]
                order = field_map["__order__"]
            return self._json_ok({
                "mode": mode,
                "images": self._build_images_payload(field_id, order, items),
            })

        if mode == "replace":
            field_map["__items__"] = PersistentMapping()
            field_map["__order__"] = []
            items = field_map["__items__"]
            order = field_map["__order__"]

        new_ids = []
        for fo in files:
            if not fo:
                continue
            blob = Blob()
            f = blob.open("w")
            data = fo.read()
            f.write(data)
            f.close()

            filename, content_type = _file_info(fo)
            img_id = uuid.uuid4().hex
            items[img_id] = {
                "id": img_id,
                "filename": filename,
                "content_type": content_type,
                "blob": blob,
            }
            order.append(img_id)
            new_ids.append(img_id)

        return self._json_ok({
            "mode": mode,
            "new_ids": new_ids,
            "images": self._build_images_payload(field_id, order, items),
        })

    def _build_images_payload(self, field_id, order, items):
        out = []
        base = self.context.absolute_url()
        for img_id in order:
            info = items.get(img_id) or {}
            filename = info.get("filename", "image")
            url = "%s/@@tmv-image?field=%s&id=%s" % (base, field_id, img_id)
            rel = "@@tmv-image?field=%s&id=%s" % (field_id, img_id)
            out.append({
                "id": img_id,
                "filename": filename,
                "url": url,
                "rel": rel,
            })
        return out

    def _json_ok(self, payload):
        payload = payload or {}
        payload["ok"] = True
        self.request.response.setHeader("Content-Type", "application/json")
        return json.dumps(payload)

    def _json_error(self, msg):
        self.request.response.setHeader("Content-Type", "application/json")
        return json.dumps({"ok": False, "error": msg})
