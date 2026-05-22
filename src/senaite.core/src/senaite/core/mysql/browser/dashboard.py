# -*- coding: utf-8 -*-
from Products.Five import BrowserView

class MySQLDashboard(BrowserView):
    """数据分析总览（空白页 + 跳转入口）
       以后可在这里做总统计（从 MySQL 拉聚合）"""
    def modules(self):
        # 先只放一个 HLA 的入口；后续在这里继续追加
        return [
            {
                "key": "hla_v1",
                "title": u"HLA 结果库",
                "href": "%s/hlav1-table" % self.context.absolute_url(),
                "desc": u"查看并检索 HLA 结果数据表"
            },
        ]
