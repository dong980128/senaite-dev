# -*- coding: utf-8 -*-
import logging
import transaction
from zope.publisher.browser import BrowserView
from zope.annotation.interfaces import IAnnotations

logger = logging.getLogger("senaite.tmv_image")
TMV_IMAGES_KEY = "tmv.images"

class TMVImageView(BrowserView):
    """
    GET /@@tmv-image?field=xxx&id=yyy     (new)
    GET /@@tmv-image?field=xxx&slot=zzz   (legacy)
    """

    def __call__(self):
        req = self.request
        field_id = req.form.get("field")
        img_id = req.form.get("id")
        slot = req.form.get("slot")

        if not field_id or (not img_id and not slot):
            req.response.setStatus(400)
            return "missing field/id(or slot)"

        # 强制让对象失效，从数据库重新加载最新数据
        try:
            self.context._p_invalidate()
        except Exception as e:
            logger.warning("[TMV-IMAGE] invalidate failed: %s", e)
            try:
                conn = self.context._p_jar
                if conn is not None:
                    conn.sync()
            except Exception:
                transaction.abort()

        ann = IAnnotations(self.context)
        store = ann.get(TMV_IMAGES_KEY) or {}
        field_map = store.get(field_id) or {}
        items = field_map.get("__items__", {}) or {}

        info = None

        # new
        if img_id:
            info = items.get(img_id)

        # legacy
        if info is None and slot:
            info = field_map.get(slot)

        if not info:
            logger.warning("[TMV-IMAGE] NOT FOUND: context=%s field=%s id=%s",
                           self.context.getId(), field_id, img_id)
            req.response.setStatus(404)
            return "not found"

        blob = info.get("blob")
        if blob is None:
            logger.warning("[TMV-IMAGE] NO BLOB: context=%s field=%s id=%s",
                           self.context.getId(), field_id, img_id)
            req.response.setStatus(404)
            return "no blob"

        content_type = info.get("content_type", "application/octet-stream")
        filename = info.get("filename", "image")

        f = blob.open("r")
        data_bytes = f.read()
        f.close()

        resp = req.response
        resp.setHeader("Content-Type", content_type)
        if isinstance(filename, unicode):
            filename = filename.encode("utf-8")
        resp.setHeader("Content-Disposition", 'inline; filename="%s"' % filename)
        return data_bytes
