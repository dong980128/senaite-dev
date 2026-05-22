# -*- coding: utf-8 -*-
from Products.Five import BrowserView
from bika.lims import api

try:
    from html import escape  # py3
except Exception:
    def escape(s):
        try:
            return s.replace(u"&", u"&amp;").replace(u"<", u"&lt;").replace(u">", u"&gt;")
        except Exception:
            return s

class InstallProjectsView(BrowserView):
    """访问一次：创建 /projects，并把默认视图设为 @@projects-by-name"""
    def __call__(self):
        portal = api.get_portal()
        out = []

        if 'projects' in portal.objectIds():
            obj = portal['projects']
            out.append(u"已存在：/projects")
        else:
            portal.invokeFactory('Folder', id='projects', title=u'项目')
            obj = portal['projects']
            out.append(u"已创建：/projects")

        # 设默认视图
        try:
            obj.setLayout('projects-by-name')
            out.append(u"默认视图已设为：projects-by-name")
        except Exception as e:
            out.append(u"设默认视图失败：%s" % e)

        # 确保显示在导航
        try:
            if getattr(obj, 'exclude_from_nav', None):
                obj.exclude_from_nav = False
                out.append(u"已取消 exclude_from_nav")
        except Exception:
            pass
        try:
            if hasattr(obj, 'setExcludeFromNav'):
                obj.setExcludeFromNav(False)
                out.append(u"setExcludeFromNav(False)")
        except Exception:
            pass

        try:
            obj.reindexObject()
        except Exception:
            pass

        return u"<pre>%s</pre>" % u"\n".join([escape(api.safe_unicode(x)) for x in out])
