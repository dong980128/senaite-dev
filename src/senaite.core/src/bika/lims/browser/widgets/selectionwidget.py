# -*- coding: utf-8 -*-
#
# This file is part of SENAITE.CORE.
#
# SENAITE.CORE is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, version 2.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc., 51
# Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# Copyright 2018-2025 by it's authors.
# Some rights reserved, see README and LICENSE.
import logging

from Products.Archetypes.Widget import SelectionWidget as _s
from Products.Archetypes.Registry import registerWidget

from AccessControl import ClassSecurityInfo
logger = logging.getLogger()

class SelectionWidget(_s):
    _properties = _s._properties.copy()
    _properties.update({
        'macro': "bika_widgets/selection",
    })

    security = ClassSecurityInfo()

    # def getOldValue(self, context):
    #     try:
    #         snapshot = getattr(context, "OldFieldSnapshot", [])
    #         # logger.info("[WIDGET] context = %s", context)
    #         # logger.info("[WIDGET] OldFieldSnapshot = %s", snapshot)
    #
    #         if not snapshot:
    #             # logger.warning("[WIDGET] OldFieldSnapshot 是空的")
    #             return None
    #
    #         field = getattr(self, "field", None)
    #         if not field:
    #             # logger.warning("[WIDGET] 当前 widget 实例没有 field 属性")
    #             return None
    #
    #         field_name = field.getName()
    #         # logger.info("[WIDGET] 当前字段名为：%s", field_name)
    #
    #         for entry in reversed(snapshot):  # 从最近的一条开始
    #             # logger.info("[WIDGET] entry = %s", entry)
    #
    #             entry_field = str(entry.get("field", "")).strip()
    #             if entry_field != field_name.strip():
    #                 continue
    #
    #             # logger.info("[WIDGET] 命中字段 '%s' 的旧值记录：%s", field_name, entry)
    #             return entry  #  返回整个 entry，而不是 entry.get("old")
    #
    #         # logger.info("[WIDGET] 字段 '%s' 在 OldFieldSnapshot 中未找到", field_name)
    #         return None
    #
    #     except Exception as e:
    #         logger.error("[WIDGET] 获取字段旧值失败：%s", str(e))
    #         return None
    def getOldValue(self, context):
        try:
            snapshot = getattr(context, "OldFieldSnapshot", [])
            field = getattr(self, "field", None)
            if not snapshot or not field:
                return []

            field_name = field.getName().strip()
            return [
                entry for entry in snapshot
                if str(entry.get("field", "")).strip() == field_name
            ]
        except Exception as e:
            logger.error("[WIDGET] 获取字段旧值失败：%s", str(e))
            return []


registerWidget(SelectionWidget)
