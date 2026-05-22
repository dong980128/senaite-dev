# -*- coding: utf-8 -*-
"""
MySQL 连接配置（优先级：环境变量 > /data/mysql.ini > 默认值）
"""
from __future__ import print_function
import os

DEFAULTS = {
    "host": "172.19.192.1",
    "port": 3306,
    "user": "lims",
    "password": "lims123",
    "database": "lims",
    "charset": "utf8mb4",
    "timeout": 5,
    "autocommit": True,  # 先简化：交给应用显式事务时再改
}


def _env(key, default):
    return os.environ.get("MYSQL_" + key, default)


# 从环境变量读取对应的配置
HOST = _env("HOST", DEFAULTS["host"])
PORT = int(_env("PORT", str(DEFAULTS["port"])))
USER = _env("USER", DEFAULTS["user"])
PASSWORD = _env("PASSWORD", DEFAULTS["password"])
DATABASE = _env("DB", _env("DATABASE", DEFAULTS["database"]))
CHARSET = _env("CHARSET", DEFAULTS["charset"])
TIMEOUT = int(_env("TIMEOUT", str(DEFAULTS["timeout"])))
AUTOCOMMIT = (_env("AUTOCOMMIT", str(DEFAULTS["autocommit"]))).lower() in ("1", "true", "yes", "on")

# 支持简单 ini：/data/mysql.ini（key=value）
INI_PATH = os.environ.get("MYSQL_INI", "/data/mysql.ini")
if os.path.exists(INI_PATH):
    try:
        vals = {}
        for line in open(INI_PATH):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = [x.strip() for x in line.split("=", 1)]
            kl = k.lower()
            if kl in ("port", "timeout"):
                vals[kl] = int(v)
            elif kl == "autocommit":
                vals[kl] = v.lower() in ("1", "true", "yes", "on")
            else:
                vals[kl] = v
        HOST = vals.get("host", HOST)
        PORT = vals.get("port", PORT)
        USER = vals.get("user", USER)
        PASSWORD = vals.get("password", PASSWORD)
        DATABASE = vals.get("database", DATABASE)
        CHARSET = vals.get("charset", CHARSET)
        TIMEOUT = vals.get("timeout", TIMEOUT)
        AUTOCOMMIT = vals.get("autocommit", AUTOCOMMIT)
    except Exception:
        # 配置解析失败逻辑处理
        pass


def as_dict():
    return dict(
        host=HOST, port=PORT, user=USER, password=PASSWORD,
        database=DATABASE, charset=CHARSET, timeout=TIMEOUT,
        autocommit=AUTOCOMMIT
    )
