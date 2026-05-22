# -*- coding: utf-8 -*-
import logging
import traceback

from bika.lims import api
from bika.lims.utils.analysis import create_analysis
from bika.lims.workflow import doActionFor as do_action_for

logger = logging.getLogger("senaite.autoassign")

# ---------
# 规则表：项目编号 → 需要自动添加的检测项目 + 分配的分析员
# 格式:
#   "项目编号": [
#       ("analysis keyword", "分析员用户名"),
#   ]
# ---------

RULES = {
    "HLA01": [
        ("hla-i-ii", "sh_hla"),
        # 可以新增加其他实验
    ],
    # 以后新增项目：
}


def auto_assign_analyst(analysis_request):

    project_name = ""
    try:
        project_name = analysis_request.getProjectName() or ""
    except Exception:
        pass

    sample_id = analysis_request.getId()
    # 查找匹配规则
    rules = _match_rules(project_name)
    if not rules:
        logger.debug("[AutoAssign] 项目 %s 无匹配规则，跳过", project_name)
        return

    for keyword, analyst in rules:
        try:
            analysis = _ensure_analysis(analysis_request, keyword)
            if not analysis:
                logger.error(
                    "[AutoAssign] 无法找到或创建 Analysis: keyword=%s", keyword)
                continue

            analysis.setAnalyst(analyst)
            analysis.reindexObject(idxs=["Analyst"])

            do_action_for(analysis, "assign")
        except Exception as e:
            logger.error(
                "[AutoAssign] 失败: 样本=%s keyword=%s 错误=%s\n%s",
                sample_id, keyword, str(e), traceback.format_exc()
            )


def _ensure_analysis(analysis_request, keyword):
    """
    检查样本是否已有指定 keyword 的 Analysis。
    没有则从 Analysis Service 创建并添加。
    返回 Analysis 对象，找不到 Service 则返回 None。
    """
    for analysis in analysis_request.getAnalyses(full_objects=True):
        if analysis.getKeyword() == keyword:
            logger.debug("[AutoAssign] Analysis 已存在: %s", keyword)
            return analysis

    catalog = api.get_tool("senaite_catalog_setup")
    results = catalog(portal_type="AnalysisService", getKeyword=keyword)
    if not results:
        logger.error("[AutoAssign] 找不到 AnalysisService: keyword=%s", keyword)
        return None

    service = results[0].getObject()

    analysis = create_analysis(analysis_request, service)
    logger.info("[AutoAssign] 已添加 Analysis: %s 到样本 %s",
                keyword, analysis_request.getId())
    return analysis


def _match_rules(project_name):
    """
    按项目编号精确匹配规则表（忽略大小写）。
    返回 [(keyword, analyst), ...] 或 None。
    """
    project_lower = project_name.strip().lower()
    for rule_project, rules in RULES.items():
        if rule_project.lower() == project_lower:
            return rules
    return None