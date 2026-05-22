# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import json
from Products.Five import BrowserView
from senaite.core.mysql.browser.analytics import (
    get_hla_avg, build_echarts_payload, search_by_haplotype_or_sample,
    get_tumor_type_java,get_populations_java,haplotype_java,)

class APIHlaAvg(BrowserView):
    def __call__(self):
        request = self.request
        source = (request.get('source') or '').strip()
        population = (request.get('population') or '').strip()

        data = build_echarts_payload(source=source, population=population)

        response = request.response
        response.setHeader("Content-Type", "application/json; charset=utf-8")
        # 允许简单跨域（如果 dashboard 里需要 fetch，本域不需要这句）
        response.setHeader("Access-Control-Allow-Origin", "*")
        return json.dumps({'code': 0, 'msg': 'ok', 'data': data})

class APIHLASearchByHaplotype(BrowserView):
    """
    访问：/@@mysql_hla_haplotype
    参数：
      - hla:       类似 "A*02:01;B*46:01;C*01:02"   （按分号分隔；可重复）
      - sampleId:  可为单个或逗号分隔的多个 sample_id
    说明：
      - 两者取其一；若都提供则优先使用 hla
      - 返回字段与 Java 版一致：{'SampleData': [...], 'GeneFrequence': {...}}
    """
    def __call__(self):
        try:
            req = self.request
            hla = (req.get('hla') or '').strip()
            sample_id = (req.get('sampleId') or '').strip()

            data = search_by_haplotype_or_sample(hla1=hla, sample_id=sample_id)

            res = self.request.response
            res.setHeader("Content-Type", "application/json; charset=utf-8")
            return json.dumps({'code': 0, 'msg': 'ok', 'data': data}, ensure_ascii=False)
        except Exception as e:
            res = self.request.response
            res.setHeader("Content-Type", "application/json; charset=utf-8")
            res.setStatus(500)
            return json.dumps({'code': 1, 'msg': 'search error: %s' % e}, ensure_ascii=False)

class APIHlTumorType(BrowserView):
    def __call__(self):
        req = self.request
        source = (req.get('Source') or '').strip()
        data = get_tumor_type_java(Source=source)
        res = req.response
        res.setHeader("Content-Type", "application/json; charset=utf-8")
        return json.dumps({'code': 0, 'msg': 'ok', 'data': data}, ensure_ascii=False)

class APIHlPopulations(BrowserView):

    def __call__(self):
        req = self.request
        source = (req.get('source') or '').strip()
        data = get_populations_java(source=source)
        res = req.response
        res.setHeader("Content-Type", "application/json; charset=utf-8")
        return json.dumps({'code': 0, 'msg': 'ok', 'data': data}, ensure_ascii=False)

class APIHlHaplotype(BrowserView):

    def __call__(self):
        req = self.request

        values = req.get('values')
        if not isinstance(values, (list, tuple)):
            values = [v.strip() for v in (values or u'').split(',') if v and v.strip()]

        Source   = (req.get('Source')   or '').strip()
        population = (req.get('population') or '').strip()
        tumorType  = (req.get('tumorType')  or '').strip()

        data = haplotype_java(values=values, tumorType=tumorType, Source=Source, population=population)

        res = req.response
        res.setHeader("Content-Type", "application/json; charset=utf-8")
        return json.dumps({'code': 0, 'msg': 'ok', 'data': data}, ensure_ascii=False)