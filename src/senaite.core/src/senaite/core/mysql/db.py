# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function
import threading
import time
from contextlib import contextmanager
import pymysql
from . import config

# 每线程一个连接
_local = threading.local()

# 这些错误码表示“连接丢失，需要重连”
_RECONNECT_ERRNOS = {2006, 2013, 2014, 2055}


def _mk_conn():
    cfg = config.as_dict()
    # 注意 DictCursor -> 返回 dict
    conn = pymysql.connect(
        host=cfg["host"],
        port=int(cfg["port"]),
        user=cfg["user"],
        passwd=cfg.get("password") or cfg.get("passwd"),
        db=cfg["database"],
        charset=cfg.get("charset", "utf8mb4"),
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=int(cfg.get("timeout", 5)),
        read_timeout=int(cfg.get("timeout", 5)),
        write_timeout=int(cfg.get("timeout", 5)),
        autocommit=bool(cfg.get("autocommit", True)),
    )

    return conn


def _ensure_conn_alive(conn):
    """用 ping(reconnect=True)代替SELECT 1，轻量且能自动重连"""
    try:
        conn.ping(reconnect=True)
        return conn
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
        return None


def get_conn():
    conn = getattr(_local, "conn", None)
    conn = _ensure_conn_alive(conn) if conn else None
    if conn is None:
        conn = _mk_conn()
        _local.conn = conn
    return conn


def close():
    conn = getattr(_local, "conn", None)
    if conn:
        try:
            conn.close()
        except Exception:
            pass
        _local.conn = None


def _with_reconnect(func, *args, **kwargs):
    """
    对 execute/query 做一层自动重连保护：
    - 如果遇到“连接丢失类”错误码，重连一次再执行
    """
    conn = get_conn()
    try:
        return func(conn, *args, **kwargs)
    except pymysql.MySQLError as e:
        errno = getattr(e, "args", [None])[0]
        if errno in _RECONNECT_ERRNOS:
            # 重连一次
            close()
            conn = get_conn()
            return func(conn, *args, **kwargs)
        raise


def query_one(sql, params=None):
    def _run(conn):
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchone()
    return _with_reconnect(_run)


def query_all(sql, params=None):
    def _run(conn):
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return list(cur.fetchall())
    return _with_reconnect(_run)


def execute(sql, params=None):
    """
    DML：INSERT/UPDATE/DELETE
    返回受影响行数；autocommit=True 时自动提交
    """
    def _run(conn):
        with conn.cursor() as cur:
            return cur.execute(sql, params or ())
    return _with_reconnect(_run)


def executemany(sql, param_list):
    def _run(conn):
        with conn.cursor() as cur:
            return cur.executemany(sql, param_list or [])
    return _with_reconnect(_run)


@contextmanager
def transaction():
    """
    事务上下文
      with db.transaction():
          db.execute(...)
          db.execute(...)
    autocommit=False 期间，异常会回滚，正常则提交
    """
    conn = get_conn()
    old = conn.get_autocommit()
    conn.autocommit(False)
    try:
        yield
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.autocommit(old)


def bulk_upsert(table, cols, rows, uniq):
    """
    批量 INSERT ... ON DUPLICATE KEY UPDATE
    - table: 字符串，如 'hlav1'
    - cols:  列名列表，顺序与 rows 中每行的 tuple 对应
    - rows:  [tuple, ...]
    - uniq:  唯一键列名元组/列表，例如 ('sample_id', 'ksh_id')
    返回成功提交的行数（尝试写入的条数）
    """
    if not rows:
        return 0

    # UPDATE 子句不更新唯一键列
    uniq = set(uniq or ())
    upd_cols = [c for c in cols if c not in uniq]
    placeholders = ",".join(["%s"] * len(cols))
    sql = (
        "INSERT INTO {tbl} ({cols}) VALUES ({ph}) "
        "ON DUPLICATE KEY UPDATE {upd}"
    ).format(
        tbl=table,
        cols=",".join(cols),
        ph=placeholders,
        upd=",".join(["{0}=VALUES({0})".format(c) for c in upd_cols]) or
            ",".join(["{0}={0}".format(c) for c in upd_cols])
    )
    executemany(sql, rows)
    return len(rows)


def ping_info():
    row = query_one("SELECT VERSION() AS version, CURRENT_USER() AS user, DATABASE() AS db")
    info = row or {}
    info["ts"] = int(time.time())
    return info