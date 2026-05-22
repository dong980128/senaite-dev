# -*- coding: utf-8 -*-
"""
analysis_utils.py
Analysis 相关工具函数 —— 所有文件都从这里导入，禁止在其他文件重复定义。
分类：
  1. AnalysisService 查询   get_analysisservice_by_token / resolve_services_by_tokens
  2. Analysis UID 解析      resolve_analysis_uids_by_service_uids / resolve_analysis_uids_by_tokens
  3. Analysis 存在性检查    ar_has_analysis_for_service
  4. Analysis 创建          create_analysis
  5. Analysis 工作流        ensure_workflow_initialized / try_transition_to_assigned / ensure_analysis_editable
  6. Analysis Catalog       catalog_analysis
  7. InterimFields          ensure_interims_on_analysis
  8. 组合操作               ensure_analyses_for_stage

"""
import contextlib
import logging
from AccessControl.SecurityManagement import (
    getSecurityManager, setSecurityManager, newSecurityManager
)
from AccessControl.User import UnrestrictedUser

from bika.lims import api
from bika.lims.utils.analysis import create_retest
from bika.lims.interfaces import IVerified
from bika.lims.utils.analysis import create_analysis as _system_create_analysis
from bika.lims.interfaces import IRejected, IRetracted
from copy import deepcopy
from zope.event import notify as znotify
from zope.interface import alsoProvides, noLongerProvides
from zope.lifecycleevent import ObjectAddedEvent
from Products.CMFCore.indexing import processQueue
from Products.CMFCore.utils import getToolByName

from senaite.core.labprocess.utils_common import (
    as_list,
    get_path,
    get_uid,
    norm_token,
    to_unicode,
    wf_state,
    to_int,
)

logger = logging.getLogger(__name__)

# Analysis 终态：这些状态下的 Analysis 视为无效，需要新建
TERMINAL_STATES = ("rejected", "retracted", "cancelled", "invalid")

try:
    from plone.dexterity.utils import createContentInContainer
except Exception:
    createContentInContainer = None


def get_analysisservice_by_token(token):
    """
    按单个 token（keyword/title/id）查找 AnalysisService 对象。
    优先走 bika_setup folder，再 catalog 兜底。
    返回对象或 None。
    """

    token = norm_token(token)
    if not token:
        return None

    portal = api.get_portal()

    # 优先 bika_setup folder（最快）
    try:
        folder = portal.bika_setup.bika_analysisservices
        for svc in folder.objectValues():
            if getattr(svc, "portal_type", "") != "AnalysisService":
                continue
            kw = norm_token(getattr(svc, "getKeyword", lambda: "")() or "")
            title = norm_token(svc.Title() if callable(getattr(svc, "Title", None)) else "")
            oid = norm_token(svc.getId() if callable(getattr(svc, "getId", None)) else "")
            if token in (kw, title, oid):
                return svc
    except Exception as e:
        logger.warning("[analysis_utils] bika_setup lookup failed: %s", e)

    # catalog 兜底
    try:
        pc = api.get_tool("portal_catalog")
        brains = pc(portal_type="AnalysisService")
        for b in brains:
            try:
                svc = b.getObject()
            except Exception:
                continue
            kw = norm_token(getattr(svc, "getKeyword", lambda: "")() or "")
            title = norm_token(svc.Title() if callable(getattr(svc, "Title", None)) else "")
            oid = norm_token(svc.getId() if callable(getattr(svc, "getId", None)) else "")
            if token in (kw, title, oid):
                return svc
    except Exception as e:
        logger.warning("[analysis_utils] catalog lookup failed: %s", e)

    return None


def resolve_services_by_tokens(service_tokens):
    """
    把 token 列表（keyword/title/id）解析成 AnalysisService 对象列表（去重）。
    统一替代 create_processrun 和 advance_taskrun 里各自的 _resolve_analysis_services。

    参数：service_tokens - list/str，如 ['Ca', 'Na'] 或 'Ca,Na'
    返回：list[AnalysisService]
    """
    tokens = [norm_token(x) for x in as_list(service_tokens) if norm_token(x)]
    if not tokens:
        return []
    seen_uids = set()
    result = []

    for token in tokens:
        svc = get_analysisservice_by_token(token)
        if not svc:
            logger.warning("[analysis_utils] AnalysisService not found for token=%r", token)
            continue
        uid = get_uid(svc)
        if uid and uid not in seen_uids:
            seen_uids.add(uid)
            result.append(svc)

    return result


def resolve_analysis_uids_by_service_uids(ar, service_uids):
    """
    给定 service UID 集合，返回 AR 下对应 Analysis 的 UID 列表。
    只返回非终态（非 rejected/retracted/cancelled/invalid）的 Analysis。
    """
    service_uids = set([to_unicode(x).strip() for x in (service_uids or []) if to_unicode(x).strip()])
    if not service_uids:
        return []

    portal = api.get_portal()

    out = []
    for a in _iter_analyses(ar):
        svc = _get_analysis_service(a)
        if not svc:
            continue
        suid = get_uid(svc)
        if suid not in service_uids:
            continue
        state = wf_state(a, portal=portal) or ""
        if state in TERMINAL_STATES:
            continue
        out.append(get_uid(a))
    return out


def resolve_analysis_uids_by_tokens(ar, tokens):
    """
    给定 token 列表，返回 AR 下对应 Analysis 的 UID 列表。
    统一替代各文件里的 _resolve_analysis_uids_by_services。
    """
    svcs = resolve_services_by_tokens(tokens)
    service_uids = [get_uid(s) for s in svcs]
    return resolve_analysis_uids_by_service_uids(ar, service_uids)


def ar_has_analysis_for_service(ar, svc):
    """
    检查 AR 下是否已经存在指定 AnalysisService 的 Analysis。
    统一替代 advance_taskrun 里的 _ar_has_analysis_for_service。
    """
    suid = get_uid(svc)
    for a in _iter_analyses(ar):
        s = _get_analysis_service(a)
        if s and get_uid(s) == suid:
            return True
    return False


def _reset_from_terminal(wf, a, uid):
    """把处于终态的 Analysis 恢复到 unassigned 状态，并清除接口标记。"""
    available = []
    try:
        available = [t["id"] for t in (wf.getTransitionsFor(a) or [])]
    except Exception:
        pass
    for action in ("unassign", "reinstate"):
        if action in available:
            try:
                wf.doActionFor(a, action)
                try:
                    noLongerProvides(a, IRejected)
                    noLongerProvides(a, IRetracted)
                except Exception:
                    pass
                return True
            except Exception:
                logger.warning("[analysis_utils] %s failed uid=%s", action, uid)
    return False


def _force_process_queue():
    try:
        processQueue()
    except Exception as e:
        logger.warning("[analysis_utils] processQueue failed: %s", e)


def create_analysis(ar, svc, analyst=None):
    """
    AR 下创建 Analysis，走系统标准路径。

    修复说明：
    - api.create 内部触发 ObjectInitializedEvent，不触发 catalog 注册
    - 需要手动触发 ObjectAddedEvent 让 CatalogMultiplexProcessor 正确注册
    - reindexObjectSecurity 修复 allowedRolesAndUsers
    - _reindex_analysis_catalog 修复 review_state FieldIndex
    - processQueue 确保批量并发时 ConflictError retry 不会丢失索引
    """
    a = _system_create_analysis(ar, svc)
    if a is None:
        return None

    # 1. 设置 Analyst
    if analyst:
        try:
            field = a.getField("Analyst") if hasattr(a, "getField") else None
            if field is not None:
                field.set(a, to_unicode(analyst).strip())
            elif hasattr(a, "setAnalyst"):
                a.setAnalyst(analyst)
        except Exception as e:
            logger.warning("[analysis_utils] setAnalyst failed: %s", e)

    # 2. 工作流初始化
    portal = api.get_portal()
    ensure_workflow_initialized(portal, a)
    try_transition_to_assigned(portal, a)

    # 3. 所有初始化完成后，强制完整重索引
    # 顺序很关键：
    # reindexObject     → 触发 getAncestorsUIDs / object_provides / is_active 等
    # reindexObjectSecurity → 触发 allowedRolesAndUsers
    # _reindex_analysis_catalog → 单独修复 review_state（FieldIndex 特殊处理）

    try:
        a.reindexObject()
    except Exception:
        pass

    try:
        a.reindexObjectSecurity()
    except Exception:
        pass

    # 3b. 触发 ObjectAddedEvent确保catalog正确注册
    # api.create 内部触发的是 ObjectInitializedEvent，不会注册 catalog
    # 必须手动触发 ObjectAddedEvent 让 CatalogMultiplexProcessor 注册
    try:
        znotify(ObjectAddedEvent(a, ar, a.getId()))
    except Exception as e:
        logger.warning("[create_analysis] ObjectAddedEvent failed uid=%s: %s", get_uid(a), e)
        try:
            a.manage_afterAdd(a, ar)
        except Exception as e2:
            logger.warning("[create_analysis] manage_afterAdd failed uid=%s: %s", get_uid(a), e2)

    _reindex_analysis_catalog(a, portal)

    # 强制立即执行 catalog 队列
    # 防止 ConflictError retry 时事务回滚导致队列清空索引丢失
    _force_process_queue()

    return a


def ensure_workflow_initialized(portal, a):
    """
    确保 Analysis 的工作流已初始化（有 chain 且有初始状态）。
    如果没有则调用 notifyCreated 补救。
    统一替代 create_processrun 里的 _ensure_workflow_initialized。
    """
    wf = getToolByName(portal, "portal_workflow")
    need_fix = False

    try:
        chain = wf.getChainFor(a)
        if not chain:
            need_fix = True
        else:
            acts = wf.listActions(object=a) or []
            if not acts:
                need_fix = True
    except Exception:
        need_fix = True

    if need_fix:
        try:
            wf.notifyCreated(a)
        except Exception:
            logger.exception("[analysis_utils] notifyCreated uid=%s failed", get_uid(a))
        try:
            a.reindexObjectSecurity()
        except Exception:
            pass
        try:
            a.reindexObject()
        except Exception:
            pass


def try_transition_to_assigned(portal, a):
    """
    把 Analysis 尽量推进到 assigned 状态。

    完整路径：
      registered         -> initialize -> unassigned -> assign -> assigned
      rejected/retracted -> reinstate  -> unassigned -> assign -> assigned
      unassigned         ->               assign -> assigned

    返回：(changed, before_state, after_state)
    """
    wf = getToolByName(portal, "portal_workflow")
    before = wf_state(a, portal=portal)
    uid = get_uid(a)

    # 已经在目标或更后的状态，直接返回
    if before in ("assigned", "to_be_verified", "verified", "published"):
        return False, before, before

    # rejected/retracted -> unassign, cancelled -> reinstate，回到 unassigned
    if before in ("rejected", "retracted", "cancelled"):
        _reset_from_terminal(wf, a, uid)

    cur = wf_state(a, portal=portal)

    # registered -> unassigned
    if cur == "registered":
        try:
            wf.doActionFor(a, "initialize")
        except Exception:
            logger.exception("[analysis_utils] initialize failed uid=%s state=%s", uid, cur)
        cur = wf_state(a, portal=portal)

    # unassigned -> assigned
    if cur in ("unassigned", "registered"):
        try:
            wf.doActionFor(a, "assign")
        except Exception:
            logger.exception("[analysis_utils] assign failed uid=%s state=%s", uid, cur)

    after = wf_state(a, portal=portal)
    changed = (after != before)
    return (changed, before, after)


def ensure_analysis_editable(portal, analysis, analyst_id=None):
    """
    确保 Analysis 处于可编辑状态（assigned），可选设置 Analyst。
    返回最终 state。
    """
    if analyst_id:
        try:
            field = analysis.getField("Analyst") if hasattr(analysis, "getField") else None
            if field is not None:
                field.set(analysis, to_unicode(analyst_id).strip())
            elif hasattr(analysis, "setAnalyst"):
                analysis.setAnalyst(analyst_id)
        except Exception:
            logger.exception("[analysis_utils] setAnalyst failed uid=%s", get_uid(analysis))

    # 复用 try_transition_to_assigned 推进状态
    _, _, after = try_transition_to_assigned(portal, analysis)
    try:
        analysis.reindexObject()
    except Exception:
        pass
    return after


def catalog_analysis(a):
    """
    确保 Analysis 在所有 catalog 中都有正确索引。
    使用系统的 api.catalog_object，它会：
      1. 用 getPhysicalPath()[2:] 正确注册到 uid_catalog（相对路径格式）
      2. 调用 reindexObject 让 CatalogMultiplexProcessor 正确更新所有 catalog
         包括 senaite_catalog_analysis 的 review_state FieldIndex
    """
    try:
        api.catalog_object(a)
    except Exception as e:
        logger.error("[analysis_utils] catalog_analysis failed uid=%s err=%s", get_uid(a), e, exc_info=True)

    _reindex_analysis_catalog(a)


# ---------------------------------------------------------------------------
# 7. InterimFields
# ---------------------------------------------------------------------------

def ensure_interims_on_analysis(a, svc):
    """
    如果 Analysis 没有 InterimFields，则从 AnalysisService 复制过来。
    统一替代 create_processrun 里的 _ensure_interims_on_analysis。
    返回 True 表示有写入，False 表示无需处理。
    """
    try:
        cur = a.getInterimFields() or []
    except Exception:
        cur = []
    if cur:
        return False

    try:
        interims = svc.getInterimFields() or []
    except Exception:
        interims = []
    if not interims:
        return False

    try:
        a.setInterimFields(deepcopy(interims))
    except Exception:
        try:
            a.setInterimFields(interims)
        except Exception:
            logger.warning("[analysis_utils] setInterimFields failed uid=%s", get_uid(a))
            return False
    return True


# ---------------------------------------------------------------------------
# 8. 组合操作
# ---------------------------------------------------------------------------

def ensure_analyses_for_stage(ar, service_tokens, analyst=None):
    """
    确保 AR 下存在 service_tokens 对应的所有 Analysis。
    已存在的跳过，不存在的创建。
    统一替代 create_processrun 里的 _ensure_analyses_for_stage。

    返回：已解析的 AnalysisService 对象列表
    """
    svcs = resolve_services_by_tokens(service_tokens)
    portal = api.get_portal()
    created = 0

    for svc in svcs:
        existing = _find_analysis_for_service(ar, svc) if ar_has_analysis_for_service(ar, svc) else None

        # 已存在但处于终态（rejected/retracted/cancelled）-> 视为不存在，新建
        if existing is not None:
            ex_state = wf_state(existing, portal=portal) or ""
            if ex_state in TERMINAL_STATES:
                existing = None

        if existing is not None:
            # 已存在且状态正常：确保可编辑、同步 interims
            ensure_interims_on_analysis(existing, svc)
            ensure_analysis_editable(portal, existing, analyst_id=analyst)
            # ConflictError retry 时 Analysis 已存在但索引可能丢失
            # 强制完整重索引确保 getAncestorsUIDs/allowedRolesAndUsers/review_state 正确
            try:
                existing.reindexObject()
            except Exception:
                pass
            try:
                existing.reindexObjectSecurity()
            except Exception:
                pass
            _reindex_analysis_catalog(existing, portal)
            _force_process_queue()
            continue

        create_analysis(ar, svc, analyst=analyst)
        created += 1

    return svcs


# ---------------------------------------------------------------------------
# 9. TaskRun 级联 Analysis transition
# ---------------------------------------------------------------------------

# TaskRun transition -> Analysis 需要执行的动作列表（按优先级）
#
# Analysis workflow 各状态可用动作（来自 definition.xml）：
#   registered     -> cancel / initialize / submit
#   unassigned     -> assign / submit / reject / cancel / start_pipeline
#   assigned       -> unassign / submit / reject / start_pipeline
#   to_be_verified -> multi_verify / verify / retest / retract / reject
#   retracted      -> unassign
#   rejected       -> unassign
#   cancelled      -> reinstate / unassign
#
# complete: 目标是把 Analysis 推进到 to_be_verified（submit）
#           如果已经在 to_be_verified 则继续 verify
# retract:  assigned/unassigned -> reject
#           to_be_verified      -> retract（会创建新 Analysis）或 reject
# invalidate: 同 retract，语义上更强
# reinstate:  特殊处理，走 try_transition_to_assigned 恢复到 assigned
# complete: 直接 submit + verify，跳过 to_be_verified
# Task 完成即视为通过，不需要额外复核步骤
# submit: assigned/unassigned -> to_be_verified
# verify: to_be_verified -> verified
_TASKRUN_TO_ANALYSIS_ACTIONS = {
    "complete": ("submit", "verify"),
    "retract": ("reject", "retract", "cancel"),
    "invalidate": ("reject", "retract", "cancel"),
    "reinstate": None,
}


def cascade_analyses_transition(taskrun, portal, taskrun_transition, submit_only=False):
    """
    根据 TaskRun 的 transition，级联处理关联 Analysis 的状态。
    只在 mode=analysis 时生效，mode=custom 直接跳过。

    映射关系：
      TaskRun complete   -> Analysis: submit -> verify
      TaskRun retract    -> Analysis: retract
      TaskRun invalidate -> Analysis: cancel（fallback retract）
      TaskRun reinstate  -> Analysis: reinstate -> assign（复用 try_transition_to_assigned）

    参数：
      taskrun            LabTaskRun 对象
      portal             portal 对象
      taskrun_transition transition 名称字符串
    """
    mode = to_unicode(getattr(taskrun, "mode", u"") or u"").strip().lower()

    if mode != u"analysis":
        return

    if taskrun_transition not in _TASKRUN_TO_ANALYSIS_ACTIONS:
        logger.warning("[CASCADE] SKIP: unknown transition=%r", taskrun_transition)
        return

    # 从 TaskRun 上取 analysis_uids
    raw_uids = as_list(getattr(taskrun, "analysis_uids", None) or [])

    if not raw_uids:
        logger.warning("[CASCADE] SKIP: analysis_uids is EMPTY on taskrun path=%s", get_path(taskrun))
        return

    wf = getToolByName(portal, "portal_workflow")
    ok = 0
    skipped = 0

    # 固定初始快照，避免循环中 raw_uids 被替换后重复处理旧 uid
    initial_uids = list(raw_uids)
    replaced = set()  # 已被新副本替换的旧 uid

    for uid in initial_uids:
        uid = to_unicode(uid).strip()
        if not uid:
            continue

        if uid in replaced:
            skipped += 1
            continue

        a = _get_obj_by_uid(uid)
        if not a:
            logger.warning("[CASCADE] uid=%s NOT FOUND in catalog", uid)
            skipped += 1
            continue

        try:
            if taskrun_transition == "reinstate":
                result = try_transition_to_assigned(portal, a)
                ok += 1
            elif taskrun_transition == "complete":
                # complete 需要依次执行 submit -> verify，两步都要走
                _cascade_complete_analysis(a, uid, wf, portal, submit_only=submit_only)
                ok += 1
            else:
                actions = _TASKRUN_TO_ANALYSIS_ACTIONS[taskrun_transition]
                successor = _cascade_one_analysis(a, uid, wf, actions, taskrun_transition, portal=portal)
                # retract 产生新副本时，更新 taskrun.analysis_uids
                if successor:
                    new_uid = to_unicode(get_uid(successor)).strip()
                    if new_uid and new_uid not in raw_uids:
                        raw_uids = [new_uid if u == uid else u for u in raw_uids]
                        try:
                            taskrun.analysis_uids = raw_uids
                            taskrun.reindexObject()
                            replaced.add(uid)  # 标记旧 uid 已被替换，防止重复 retest
                        except Exception:
                            logger.exception("[CASCADE] failed to update analysis_uids")
                            # 新副本从 registered 推进到 assigned，并设置 analyst
                        try:
                            analyst = to_unicode(
                                (as_list(getattr(taskrun, "assigned_users", None) or []) or [u""])[0]
                            ).strip() or None
                            ensure_analysis_editable(portal, successor, analyst_id=analyst)
                        except Exception:
                            logger.exception("[CASCADE] failed to push successor to assigned")
                        # 强制索引新副本到所有 catalog
                        _force_catalog_index(portal, successor)
                ok += 1

        except Exception:
            logger.exception("[CASCADE] EXCEPTION uid=%s transition=%r", uid, taskrun_transition)
            skipped += 1


def _verify_as_system(a, uid, wf, portal):
    """
    以系统权限执行 verify transition。
    verify 需要 LabManager/Verifier 角色，但 TaskRun complete 是系统级联行为，
    不应受当前登录用户角色限制。
    使用 AccessControl 的 SecurityManager 临时提升权限。
    """
    old_sm = getSecurityManager()
    try:
        # 临时切换到不受限用户执行 verify
        tmp_user = UnrestrictedUser("system", "", ["Manager"], [])
        tmp_user = tmp_user.__of__(portal.acl_users)
        newSecurityManager(None, tmp_user)

        available = set(t["id"] for t in (wf.getTransitionsFor(a) or []))

        if "verify" in available:
            wf.doActionFor(a, "verify")
        else:
            logger.warning("[CASCADE] verify_as_system uid=%s verify not available", uid)
    finally:
        # 恢复原始权限
        setSecurityManager(old_sm)


def _cascade_complete_analysis(a, uid, wf, portal, submit_only=False):
    """
    TaskRun complete 时对单个 Analysis 的处理。

    submit_only=True  （有下一步时）：只 submit 到 to_be_verified，不 verify。
    submit_only=False （最后一步时）：submit + verify，推到 verified。

    如果 Analysis 已经是 verified/published，直接跳过。
    """
    state = wf_state(a, portal=portal)
    if state in ("verified", "published"):
        return
    # step1: submit (assigned/unassigned -> to_be_verified)
    if state in ("assigned", "unassigned"):
        try:
            available = set(t["id"] for t in (wf.getTransitionsFor(a) or []))
            if "submit" in available:
                wf.doActionFor(a, "submit")
                state = wf_state(a, portal=portal)
            else:
                logger.warning("[CASCADE] complete uid=%s submit not available, available=%s",
                               uid, available)
        except Exception:
            logger.exception("[CASCADE] complete uid=%s submit failed", uid)

    # step2: verify (to_be_verified -> verified)
    # submit_only=True 表示有下一步 TaskRun，Analysis 留在 to_be_verified 等待后续完成
    # submit_only=False 表示这是最后一步，直接 verify 到 verified
    if submit_only:
        pass
    elif state == "to_be_verified":
        try:
            _verify_as_system(a, uid, wf, portal)
            state = wf_state(a, portal=portal)
        except Exception:
            logger.exception("[CASCADE] complete uid=%s verify failed", uid)

    # reindexObject 对 senaite_catalog_analysis 的 review_state 无效
    # （FieldIndex 读的是对象直属属性，Analysis 没有该属性）
    # 必须用 uncatalog + catalog 强制刷新
    _reindex_analysis_catalog(a, portal)

    final = wf_state(a, portal=portal)


def _find_successor_analysis(a):
    """
    retract 后会在同一 AR 下创建新副本。
    新副本和旧对象有相同的 AnalysisService，且 id 更新（末尾加数字）。
    通过查找同 service、创建时间更新的 Analysis 来定位。
    """
    try:
        # 优先用原生 getRetest()（通过 RetestOf relationship backreference 查找）
        fn = getattr(a, "getRetest", None)
        if callable(fn):
            try:
                successor = fn()
                if successor:
                    return successor
            except Exception:
                pass

        # fallback：旧接口兼容
        for getter in ("getSuccessor", "getRetestAnalysis"):
            fn = getattr(a, getter, None)
            if callable(fn):
                try:
                    successor = fn()
                    if successor:
                        return successor
                except Exception:
                    pass

        logger.warning("[analysis_utils] no successor found for uid=%s", get_uid(a))
        return None

    except Exception:
        logger.exception("[analysis_utils] _find_successor_analysis failed uid=%s", get_uid(a))
        return None


def _cascade_retest_analysis(a, uid, wf, portal):
    """
    verified 状态下 TaskRun retract 的处理：调用 create_retest() 创建新副本，
    旧 Analysis 保持 verified，新副本供调用方更新 taskrun.analysis_uids。
    """
    try:
        available = set(t["id"] for t in (wf.getTransitionsFor(a) or []))

        if "retest" not in available:
            logger.warning("[analysis_utils] cascade_retest uid=%s retest not available", uid)
            return None

        # 直接调用 create_retest，返回新副本对象（不依赖 catalog）
        # 同时标记旧对象为 IVerified（与系统 after_retest 行为一致）
        retest = create_retest(a)
        alsoProvides(a, IVerified)
        try:
            a.reindexObject()
        except Exception:
            pass

        # create_retest 绕过了 doActionFor，手动触发索引
        _force_catalog_index(portal, retest)

        return retest

    except Exception:
        logger.exception("[analysis_utils] cascade_retest uid=%s failed", uid)
        return None


def _cascade_one_analysis(a, uid, wf, actions, taskrun_transition, portal=None):
    """
    对单个 Analysis 按 actions 列表依次尝试 transition。

    retract/invalidate 时按状态选择动作：
      assigned/unassigned  → reject（直接作废，不产生副本）
      to_be_verified       → reject（同上）
      verified             → retest（产生新副本，旧结果保留，符合审计规范）

    返回：新副本 Analysis 对象（retest/retract 时）或 None
    """
    # verified 状态特殊处理：走 retest 而非 rejectA
    # retest 语义：结果需要重做，旧的 verified 记录保留，新副本回到 unassigned

    state_before = wf_state(a)
    if state_before == "verified" and taskrun_transition in ("retract", "invalidate"):
        return _cascade_retest_analysis(a, uid, wf, portal)

    # 获取当前允许的 transition
    try:
        available = set(t["id"] for t in (wf.getTransitionsFor(a) or []))
    except Exception:
        available = set()

    for action in actions:
        if action not in available:
            continue
        try:
            wf.doActionFor(a, action)
            _reindex_analysis_catalog(a, portal)

            # retract 会创建新副本，找到并返回
            if action == "retract":
                successor = _find_successor_analysis(a)
                if successor:
                    return successor
                return None

            return None  # 其他动作不产生新副本
        except Exception:
            logger.warning("[analysis_utils] cascade uid=%s action=%r failed", uid, action, exc_info=True)

    logger.warning("[analysis_utils] cascade uid=%s transition=%r all actions failed available=%s",
                   uid, taskrun_transition, available)
    return None


def _get_obj_by_uid(uid):
    """按 UID 获取对象，依次尝试 uid_catalog -> senaite_catalog_analysis -> portal_catalog。"""
    uid = to_unicode(uid).strip()
    if not uid:
        return None
    try:
        return api.get_object_by_uid(uid)
    except Exception:
        pass
    for catalog_name in ("senaite_catalog_analysis", "portal_catalog"):
        try:
            cat = api.get_tool(catalog_name)
            brains = cat(UID=uid)
            if brains:
                return brains[0].getObject()
        except Exception:
            pass
    return None


# ---------------------------------------------------------------------------
# 内部辅助函数（不对外暴露）
# ---------------------------------------------------------------------------

def _iter_analyses(ar):
    """遍历 AR 下所有 Analysis 对象。"""
    try:
        return list(ar.objectValues("Analysis"))
    except Exception:
        return [o for o in ar.objectValues() if getattr(o, "portal_type", None) == "Analysis"]


def _get_analysis_service(analysis):
    """安全获取 Analysis 对应的 AnalysisService 对象。"""
    try:
        svc = analysis.getAnalysisService()
        if svc:
            return svc
    except Exception:
        pass
    try:
        return analysis.getService()
    except Exception:
        return None


def _find_analysis_for_service(ar, svc):
    """在 AR 下找到对应 service 的第一个非终态 Analysis 对象。"""
    suid = get_uid(svc)
    portal = api.get_portal()
    for a in _iter_analyses(ar):
        s = _get_analysis_service(a)
        if not s or get_uid(s) != suid:
            continue
        state = wf_state(a, portal=portal) or ""
        if state in TERMINAL_STATES:
            continue
        return a
    return None


def _force_catalog_index(portal, obj):
    """强制把对象注册到所有 catalog（用于绕过正常事件流创建的对象）。"""
    try:
        path = "/".join(obj.getPhysicalPath())
        for cat_name in ("uid_catalog", "senaite_catalog_analysis",
                         "senaite_catalog", "portal_catalog"):
            try:
                portal[cat_name].catalog_object(obj, path)
            except Exception:
                pass
        obj.reindexObject()
    except Exception:
        logger.exception("[analysis_utils] _force_catalog_index failed uid=%s", get_uid(obj))


def _reindex_analysis_catalog(a, portal=None):
    """
    强制刷新 Analysis 在所有 catalog 里的 review_state 索引。

    原因：senaite_catalog_analysis 的 review_state 是 FieldIndex，
    读的是对象直属属性 a.review_state。但 Analysis 没有该属性
    （状态存在 workflow_history 里），导致 reindexObject 时 FieldIndex
    找不到属性，保留旧值不更新。

    解决：临时注入属性 → uncatalog → catalog → 删临时属性。
    原始逻辑不受影响（workflow_history 不被修改）。
    """
    if portal is None:
        try:
            portal = api.get_portal()
        except Exception:
            return
    try:
        wf = getToolByName(portal, "portal_workflow")
        state = wf.getInfoFor(a, "review_state", None)
        if not state:
            a.reindexObject()
            return

        a.review_state = state
        a._p_changed = True

        # 用 reindexObject(idxs=[...])
        # 临时注入 review_state 属性确保 FieldIndex 能读到正确值
        try:
            a.reindexObject(idxs=["review_state"])
        except Exception:
            try:
                a.reindexObject()
            except Exception:
                pass

        try:
            del a.review_state
            a._p_changed = True
        except Exception:
            pass

    except Exception:
        # 任何失败都 fallback 到普通 reindexObject，不影响主流程
        try:
            a.reindexObject()
        except Exception:
            pass


def _get_cur_tcr_selected(run, portal, cur_step):
    """
    读当前步（cur_step）Analysis 里 tcr_selector InterimField 的勾选结果。
    只返回 __checked__=True 的行，按优先级升序排序，没有勾选数据返回 None。
    只读当前 running 状态的 TaskRun，跳过已 retracted/invalid/cancelled 的。
    """
    from senaite.core.labprocess.parse_excel_util import (
        tcr_data_from_json, tcr_data_to_json
    )

    wf = getToolByName(portal, "portal_workflow")

    for tr_id in run.objectIds():
        tr = run.get(tr_id)
        if not tr or getattr(tr, "portal_type", "") != "LabTaskRun":
            continue
        step = to_int(getattr(tr, "step", 0) or 0)
        if step != cur_step:
            continue

        # 只读当前活跃的 TaskRun，跳过已终态的
        state = wf.getInfoFor(tr, "review_state", "") or ""
        if state in ("retracted", "invalid", "cancelled"):
            continue

        for analysis_uid in (getattr(tr, "analysis_uids", []) or []):
            try:
                analysis = api.get_object_by_uid(analysis_uid, default=None)
                if not analysis:
                    brains = api.get_tool("senaite_catalog_analysis")(UID=analysis_uid)
                    if not brains:
                        continue
                    analysis = brains[0].getObject()

                for interim in (analysis.getInterimFields() or []):
                    if interim.get("result_type") != "tcr_selector":
                        continue
                    raw = to_unicode(interim.get("value", "") or "")
                    parsed = tcr_data_from_json(raw)
                    if not parsed:
                        continue

                    checked_rows = [
                        r for r in (parsed.get("rows") or [])
                        if r.get("__checked__") is True
                           or str(r.get("__checked__", "")).lower() == "true"
                    ]
                    if not checked_rows:
                        continue

                    def _priority_key(r):
                        p = r.get("__priority__") or ""
                        try:
                            return (0, int(p))
                        except (ValueError, TypeError):
                            try:
                                return (0, float(p))
                            except (ValueError, TypeError):
                                return (1, p)

                    checked_rows = sorted(checked_rows, key=_priority_key)
                    return tcr_data_to_json({
                        "columns": parsed.get("columns", []),
                        "rows": checked_rows,
                    })
            except Exception:
                logger.exception(
                    "[writeback] _get_cur_tcr_selected failed analysis_uid=%s",
                    analysis_uid
                )
                continue
    return None


def _writeback_tcr_selected_to_prev(run, ar, portal, cur_step):
    """
    把当前步 tcr_selector 的勾选结果回写到紧邻上一步的 result_tcr_data keyword。
    只有当前步确实有 tcr_selector 且有勾选数据时才执行。
    不限制上一步状态（running/done 都可以），用系统权限绕过只读保护。
    """
    from senaite.core.labprocess.parse_excel_util import tcr_data_from_json
    from AccessControl.SecurityManagement import (
        getSecurityManager, setSecurityManager, newSecurityManager
    )
    from AccessControl.User import UnrestrictedUser
    from copy import deepcopy

    # 1. 读当前步勾选结果，没有直接返回
    cur_selected_json = _get_cur_tcr_selected(run, portal, cur_step)
    if not cur_selected_json:
        logger.debug(
            "[writeback] no tcr_selector data at step=%s, skip writeback", cur_step
        )
        return
    cur_selected_json = _inject_tcr_code(cur_selected_json, ar)

    logger.info("[writeback] got cur_selected_json at step=%s, len=%s",
                cur_step, len(cur_selected_json))

    # 2. 找紧邻上一步的 TaskRun
    wf = getToolByName(portal, "portal_workflow")
    candidates = []
    for tr_id in run.objectIds():
        tr = run.get(tr_id)
        if not tr or getattr(tr, "portal_type", "") != "LabTaskRun":
            continue
        state = wf.getInfoFor(tr, "review_state", "") or ""
        if state in ("retracted", "invalid", "cancelled"):
            continue
        step = to_int(getattr(tr, "step", 0) or 0)
        if step < cur_step:
            candidates.append((step, tr))

    if not candidates:
        logger.debug("[writeback] no prev taskrun found, skip")
        return

    candidates.sort(key=lambda x: x[0], reverse=True)
    _, prev_tr = candidates[0]

    logger.info("[writeback] found prev taskrun step=%s analysis_uids=%s",
                getattr(prev_tr, "step", "?"),
                getattr(prev_tr, "analysis_uids", []))

    # 3. 用系统权限写入
    _portal = api.get_portal()

    for analysis_uid in (getattr(prev_tr, "analysis_uids", []) or []):
        try:
            analysis = api.get_object_by_uid(analysis_uid, default=None)
            if not analysis:
                brains = api.get_tool("senaite_catalog_analysis")(UID=analysis_uid)
                if not brains:
                    logger.warning("[writeback] analysis not found uid=%s", analysis_uid)
                    continue
                analysis = brains[0].getObject()

            interims = analysis.getInterimFields() or []

            before_val = next(
                (i.get("value", "") for i in interims if i.get("keyword") == "result_tcr_data"),
                "NOT_FOUND"
            )
            logger.info("[writeback] BEFORE uid=%s state=%s value_len=%s",
                        analysis_uid,
                        wf_state(analysis, portal=_portal),
                        len(before_val) if before_val not in ("NOT_FOUND", "") else repr(before_val))

            changed = False
            for interim in interims:
                if interim.get("keyword") == "result_tcr_data":
                    interim["value"] = cur_selected_json
                    changed = True
                    break

            if not changed:
                logger.warning(
                    "[writeback] result_tcr_data keyword not found uid=%s", analysis_uid
                )
                continue

            old_sm = getSecurityManager()
            try:
                tmp_user = UnrestrictedUser("system", "", ["Manager"], [])
                tmp_user = tmp_user.__of__(_portal.acl_users)
                newSecurityManager(None, tmp_user)

                # 直接写底层存储，绕过 field 和工作流检查
                analysis.__dict__["InterimFields"] = deepcopy(interims)
                analysis._p_changed = True

            finally:
                setSecurityManager(old_sm)

            # 写入后读内存确认
            mem_val = next(
                (i.get("value", "") for i in (analysis.getInterimFields() or [])
                 if i.get("keyword") == "result_tcr_data"),
                "NOT_FOUND"
            )
            logger.info("[writeback] AFTER uid=%s value_len=%s",
                        analysis_uid,
                        len(mem_val) if mem_val not in ("NOT_FOUND", "") else repr(mem_val))

        except Exception:
            logger.exception(
                "[writeback] failed writing to analysis_uid=%s", analysis_uid
            )
# ---------------------------------------------------------------------------
# 10. TCR 数据注入与回写（对外公开，供 advance_taskrun 调用）
# ---------------------------------------------------------------------------

def get_prev_file_uids(run, portal, cur_step):
    """
    从紧邻上一步已完成的 TaskRun 里收集 result_type=file 的 InterimField 值。
    返回去重后的 Attachment UID 列表，保持发现顺序。
    只取最近一步有文件的 TaskRun，避免多步上传文件时混淆。
    """
    wf = getToolByName(portal, "portal_workflow")
    candidates = []
    for tr_id in run.objectIds():
        tr = run.get(tr_id)
        if not tr or getattr(tr, "portal_type", "") != "LabTaskRun":
            continue
        state = wf.getInfoFor(tr, "review_state", "") or ""
        if state != "done":
            continue
        step = to_int(getattr(tr, "step", 0) or 0)
        if step < cur_step:
            candidates.append((step, tr))

    candidates.sort(key=lambda x: x[0], reverse=True)

    uids = []
    seen = set()
    for step, tr in candidates:
        try:
            for analysis_uid in (getattr(tr, "analysis_uids", []) or []):
                brains = api.get_tool("senaite_catalog_analysis")(UID=analysis_uid)
                if not brains:
                    continue
                analysis = brains[0].getObject()
                for interim in (analysis.getInterimFields() or []):
                    if interim.get("result_type") == "file":
                        val = to_unicode(interim.get("value", "") or "").strip()
                        if val and val not in seen:
                            seen.add(val)
                            uids.append(val)
        except Exception:
            continue
        if uids:
            break

    return uids

def _inject_tcr_code(selected_json, ar):
    """
    在每行数据里注入 __tcr_code__ 字段。
    格式：SubjectUID-优先级，例：KXV0101245LHJK-1
    """
    from senaite.core.labprocess.parse_excel_util import (
        tcr_data_from_json, tcr_data_to_json
    )

    if not selected_json or not ar:
        return selected_json

    try:
        subject_uid = u""
        if hasattr(ar, "getSubjectUID"):
            subject_uid = to_unicode(ar.getSubjectUID() or u"").strip()

        parsed = tcr_data_from_json(selected_json)
        if not parsed:
            return selected_json

        rows = parsed.get("rows", [])
        for row in rows:
            priority = to_unicode(row.get("__priority__", "") or "").strip()
            if subject_uid and priority:
                row["__tcr_code__"] = u"%s-%s" % (subject_uid, priority)
            else:
                row["__tcr_code__"] = u""

        return tcr_data_to_json({
            "columns": parsed.get("columns", []),
            "rows": rows,
        })
    except Exception:
        logger.exception("[inject_tcr_code] failed")
        return selected_json

def _write_interim_value_by_type(ar, result_type, json_val):
    """
    把 json_val 写入 AR 下所有匹配 result_type 的 InterimField（value 为空才写入）。
    """
    ac = api.get_tool("senaite_catalog_analysis")
    ar_path = "/".join(ar.getPhysicalPath())
    brains = ac(
        portal_type="Analysis",
        path={"query": ar_path, "depth": 5},
        review_state=["registered", "unassigned", "assigned"],
    )
    for brain in brains:
        try:
            analysis = brain.getObject()
            interims = analysis.getInterimFields() or []
            changed = False
            for interim in interims:
                if interim.get("result_type") == result_type:
                    if not interim.get("value"):
                        interim["value"] = json_val
                        changed = True
            if changed:
                analysis.setInterimFields(interims)
                analysis.reindexObject()
        except Exception:
            logger.exception(
                "[inject_tcr] _write_interim_value_by_type failed brain=%s",
                brain.getPath()
            )


def _get_prev_tcr_selected(run, portal, cur_step):
    """
    从紧邻上一步已完成的 TaskRun 里，读取 tcr_selector InterimField 的勾选结果。
    只返回 __checked__=True 的行，没有勾选数据返回 None。
    """
    from senaite.core.labprocess.parse_excel_util import (
        tcr_data_from_json, tcr_data_to_json
    )
    wf = getToolByName(portal, "portal_workflow")
    candidates = []
    for tr_id in run.objectIds():
        tr = run.get(tr_id)
        if not tr or getattr(tr, "portal_type", "") != "LabTaskRun":
            continue
        state = wf.getInfoFor(tr, "review_state", "") or ""
        if state != "done":
            continue
        step = to_int(getattr(tr, "step", 0) or 0)
        if step < cur_step:
            candidates.append((step, tr))

    candidates.sort(key=lambda x: x[0], reverse=True)
    for step, tr in candidates:
        for analysis_uid in (getattr(tr, "analysis_uids", []) or []):
            try:
                brains = api.get_tool("senaite_catalog_analysis")(UID=analysis_uid)
                if not brains:
                    continue
                analysis = brains[0].getObject()
                for interim in (analysis.getInterimFields() or []):
                    if interim.get("result_type") != "tcr_selector":
                        continue
                    raw = to_unicode(interim.get("value", "") or "")
                    parsed = tcr_data_from_json(raw)
                    if not parsed:
                        continue
                    checked_rows = [
                        r for r in (parsed.get("rows") or [])
                        if r.get("__checked__") is True
                           or str(r.get("__checked__", "")).lower() == "true"
                    ]
                    if not checked_rows:
                        continue

                    def _priority_key(r):
                        p = r.get("__priority__") or ""
                        try:
                            return (0, int(p))
                        except (ValueError, TypeError):
                            try:
                                return (0, float(p))
                            except (ValueError, TypeError):
                                return (1, p)

                    checked_rows = sorted(checked_rows, key=_priority_key)
                    return tcr_data_to_json({
                        "columns": parsed.get("columns", []),
                        "rows": checked_rows,
                    })
            except Exception:
                logger.exception(
                    "[writeback] _get_prev_tcr_selected failed step=%s", step
                )
                continue
    return None


def _get_prev_preparation_selected(run, portal, cur_step):
    """
    从紧邻上一步已完成的 TaskRun 里，读取 tcr_preparation InterimField 里
    __preparation__=True 的行。没有数据返回 None。
    """
    from senaite.core.labprocess.parse_excel_util import (
        tcr_data_from_json, tcr_data_to_json
    )

    wf = getToolByName(portal, "portal_workflow")
    candidates = []
    for tr_id in run.objectIds():
        tr = run.get(tr_id)
        if not tr or getattr(tr, "portal_type", "") != "LabTaskRun":
            continue
        state = wf.getInfoFor(tr, "review_state", "") or ""
        if state != "done":
            continue
        step = to_int(getattr(tr, "step", 0) or 0)
        if step < cur_step:
            candidates.append((step, tr))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)

    for _, tr in candidates:
        for analysis_uid in (getattr(tr, "analysis_uids", []) or []):
            try:
                analysis = api.get_object_by_uid(analysis_uid, default=None)
                if not analysis:
                    continue
                for interim in (analysis.getInterimFields() or []):
                    if interim.get("result_type") != "tcr_preparation":
                        continue
                    raw = to_unicode(interim.get("value", "") or "")
                    parsed = tcr_data_from_json(raw) if raw else None
                    if not parsed:
                        continue
                    all_rows = parsed.get("rows", [])
                    # __preparation__=True 的行，没有则返回全部
                    checked = [r for r in all_rows if r.get("__preparation__")]
                    if not checked:
                        checked = all_rows
                    if not checked:
                        continue
                    # 初始化骨架字段
                    for row in checked:
                        row.setdefault("__scaffold1__", u"")
                        row.setdefault("__scaffold2__", u"")
                    return tcr_data_to_json({
                        "columns": parsed.get("columns", []),
                        "rows": checked,
                    })
            except Exception:
                logger.exception(
                    "[inject_tcr] _get_prev_preparation_selected failed uid=%s", analysis_uid
                )
                continue
    return None

def _get_prev_scaffold_expanded(run, portal, cur_step):
    """
    从上一步 tcr_scaffold 字段展开骨架数据：
    每个 TCR序列代码 × 每个骨架条目 = 一行。
    生成的行包含：__tcr_code__, __priority__, __scaffold__, __quantity__,
                  __plasmid_no__（质粒编号）, __plasmid_name__（质粒名称）
    以及原始 clonotype 数据列。
    """
    from senaite.core.labprocess.parse_excel_util import (
        tcr_data_from_json, tcr_data_to_json
    )

    wf = getToolByName(portal, "portal_workflow")
    candidates = []
    for tr_id in run.objectIds():
        tr = run.get(tr_id)
        if not tr or getattr(tr, "portal_type", "") != "LabTaskRun":
            continue
        state = wf.getInfoFor(tr, "review_state", "") or ""
        if state != "done":
            continue
        step = to_int(getattr(tr, "step", 0) or 0)
        if step < cur_step:
            candidates.append((step, tr))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)

    for _, tr in candidates:
        for analysis_uid in (getattr(tr, "analysis_uids", []) or []):
            try:
                analysis = api.get_object_by_uid(analysis_uid, default=None)
                if not analysis:
                    continue
                for interim in (analysis.getInterimFields() or []):
                    if interim.get("result_type") != "tcr_scaffold":
                        continue
                    raw = to_unicode(interim.get("value", "") or "")
                    parsed = tcr_data_from_json(raw) if raw else None
                    if not parsed:
                        continue

                    columns = parsed.get("columns", [])
                    all_rows = parsed.get("rows", [])

                    # 展开：每行 × 每个骨架条目 = 一行
                    expanded = []
                    for row in all_rows:
                        scaffolds = row.get("__scaffolds__") or [{"scaffold": "", "quantity": ""}]
                        for sc in scaffolds:
                            new_row = deepcopy(row)
                            new_row["__scaffold__"]    = sc.get("scaffold", u"")
                            new_row["__quantity__"]    = sc.get("quantity", u"")
                            new_row["__plasmid_no__"]  = u""
                            new_row["__plasmid_name__"] = u""
                            expanded.append(new_row)

                    if not expanded:
                        continue

                    return tcr_data_to_json({
                        "columns": columns,
                        "rows": expanded,
                    })
            except Exception:
                logger.exception(
                    "[inject_tcr] _get_prev_scaffold_expanded failed uid=%s", analysis_uid
                )
                continue
    return None

def inject_tcr_data_if_needed(run, ar, stage, portal):
    """
    创建下一步 TaskRun 时调用：
    1. file → tcr_selector：从上一步 file 字段读取 excel 写入下一步 tcr_selector。
    2. tcr_selector 勾选结果 → tcr_preparation：从上一步 tcr_selector 读取已勾选行
       写入下一步 tcr_preparation。
    3. tcr_preparation 制备结果 → tcr_scaffold：从上一步 tcr_preparation 读取已制备行
       写入下一步 tcr_scaffold。
    """
    from senaite.core.labprocess.parse_excel_util import (
        parse_and_merge_excel_attachments,
        tcr_data_to_json,
        tcr_data_from_json,
    )

    cur_step = to_int(stage.get("step", 0))

    # ===== file → tcr_selector =====
    prev_file_uids = get_prev_file_uids(run, portal, cur_step)
    if prev_file_uids:
        attachments = []
        for uid in prev_file_uids:
            try:
                att = api.get_object_by_uid(uid, default=None)
                if att:
                    attachments.append(att)
                else:
                    logger.warning("[inject_tcr] attachment not found uid=%s", uid)
            except Exception:
                logger.warning("[inject_tcr] error fetching attachment uid=%s", uid)

        if attachments:
            parsed = parse_and_merge_excel_attachments(attachments)
            if parsed:
                _write_interim_value_by_type(ar, "tcr_selector", tcr_data_to_json(parsed))
            else:
                logger.warning("[inject_tcr] parse/merge excel failed")

    # ===== tcr_selector 勾选结果 → tcr_preparation =====
    selected_json = _get_prev_tcr_selected(run, portal, cur_step)
    if selected_json:
        selected_json =  _inject_tcr_code(selected_json, ar)
        _write_interim_value_by_type(ar, "tcr_preparation", selected_json)

    # ===== tcr_preparation 制备结果 → tcr_scaffold =====
    preparation_json = _get_prev_preparation_selected(run, portal, cur_step)
    if preparation_json:
        _write_interim_value_by_type(ar, "tcr_scaffold", preparation_json)

    # ===== tcr_scaffold → tcr_plasmid：展开骨架行生成质粒列表 =====
    plasmid_json = _get_prev_scaffold_expanded(run, portal, cur_step)
    if plasmid_json:
        _write_interim_value_by_type(ar, "tcr_plasmid", plasmid_json)


def clearback_tcr_selected_to_prev(run, ar, portal, cur_step):
    """
    当前步 retract/invalidate 时，清空上一步 result_tcr_data 的回写数据。
    让上游回显恢复到空状态，等待重新 complete 后再次写入。
    不限制上一步状态，running/done 都可以清空。
    """
    from AccessControl.SecurityManagement import (
        getSecurityManager, setSecurityManager, newSecurityManager
    )
    from AccessControl.User import UnrestrictedUser

    if ar is not None:
        ac = api.get_tool("senaite_catalog_analysis")
        ar_path = "/".join(ar.getPhysicalPath())
        all_brains = ac(portal_type="Analysis", path={"query": ar_path, "depth": 5})
        for b in all_brains:
            try:
                obj = b.getObject()
                kw = getattr(obj, "getKeyword", lambda: "")() or ""
                uid = get_uid(obj)
                state = wf_state(obj, portal=portal) or ""
                logger.info("[debug-scan] keyword=%s uid=%s state=%s", kw, uid, state)
            except Exception:
                pass

    wf = getToolByName(portal, "portal_workflow")
    candidates = []
    for tr_id in run.objectIds():
        tr = run.get(tr_id)
        if not tr or getattr(tr, "portal_type", "") != "LabTaskRun":
            continue
        state = wf.getInfoFor(tr, "review_state", "") or ""
        # 打印所有 TaskRun 的 step/state，便于看全貌
        logger.info("[clearback] scan tr_id=%s step=%s state=%s",
                    tr_id,
                    getattr(tr, "step", "?"),
                    state)
        if state in ("retracted", "invalid", "cancelled"):
            continue
        step = to_int(getattr(tr, "step", 0) or 0)
        if step < cur_step:
            candidates.append((step, tr))

    if not candidates:
        logger.info("[clearback] no candidates found for cur_step=%s", cur_step)
        return

    candidates.sort(key=lambda x: x[0], reverse=True)
    _, prev_tr = candidates[0]

    # 打印选中的 prev_tr 信息
    logger.info("[clearback] selected prev_tr id=%s step=%s analysis_uids=%s",
                prev_tr.getId(),
                getattr(prev_tr, "step", "?"),
                getattr(prev_tr, "analysis_uids", []))

    _portal = api.get_portal()
    for analysis_uid in (getattr(prev_tr, "analysis_uids", []) or []):
        try:
            analysis = api.get_object_by_uid(analysis_uid, default=None)
            if not analysis:
                brains = api.get_tool("senaite_catalog_analysis")(UID=analysis_uid)
                if not brains:
                    logger.warning("[clearback] analysis not found uid=%s", analysis_uid)
                    continue
                analysis = brains[0].getObject()

            interims = analysis.getInterimFields() or []

            # 清空前的值
            before_val = next(
                (i.get("value", "") for i in interims if i.get("keyword") == "result_tcr_data"),
                "NOT_FOUND"
            )
            logger.info("[clearback] BEFORE uid=%s result_tcr_data=%s",
                        analysis_uid, repr(before_val)[:120])

            changed = False
            for interim in interims:
                if interim.get("keyword") == "result_tcr_data":
                    interim["value"] = u""
                    changed = True
                    break

            if not changed:
                logger.warning("[clearback] result_tcr_data keyword NOT FOUND uid=%s", analysis_uid)
                continue

            old_sm = getSecurityManager()
            try:
                tmp_user = UnrestrictedUser("system", "", ["Manager"], [])
                tmp_user = tmp_user.__of__(_portal.acl_users)
                newSecurityManager(None, tmp_user)

                # 直接写底层存储，绕过 field 和工作流检查
                from copy import deepcopy
                analysis.__dict__["InterimFields"] = deepcopy(interims)
                analysis._p_changed = True

            finally:
                setSecurityManager(old_sm)

            # 清空后确认
            after_val = next(
                (i.get("value", "") for i in (analysis.getInterimFields() or [])
                 if i.get("keyword") == "result_tcr_data"),
                "NOT_FOUND"
            )
            logger.info("[clearback] AFTER uid=%s result_tcr_data=%s",
                        analysis_uid, repr(after_val)[:60])

        except Exception:
            logger.exception("[clearback] failed clearing analysis_uid=%s", analysis_uid)


def writeback_tcr_selected_to_prev(run, ar, portal, cur_step):
    """
    对外公开的回写入口，供 advance_taskrun 在 complete 时调用。
    把当前步 tcr_selector 的勾选结果回写到上一步的 result_tcr_data（tcr_preparation 类型）字段。
    只有当前步确实有 tcr_selector 且有勾选数据时才执行，其他步骤调用此方法安全无副作用。
    """
    _writeback_tcr_selected_to_prev(run, ar, portal, cur_step)