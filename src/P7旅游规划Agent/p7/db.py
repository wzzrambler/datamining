import os
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import bindparam

HOME_DIR = Path(__file__).resolve().parents[1]
REPO = HOME_DIR.parents[1]


def setting(name, default=None):
    value = os.environ.get(name)
    if value is not None:
        return value
    path = HOME_DIR / '.env.local'
    if path.is_file():
        for line in path.read_text('utf-8-sig').splitlines():
            key, sep, value = line.partition('=')
            if sep and key.strip() == name:
                return value.strip()
    return default


def database_url():
    url = setting('P7_1_DATABASE_URL')
    if not url:
        raise RuntimeError('请配置 P7_1_DATABASE_URL')
    return url


_engine = None


def engine():
    global _engine
    if _engine is None:
        _engine = create_engine(database_url(), pool_pre_ping=True, hide_parameters=True)
    return _engine


def query(sql, *json_fields):
    stmt = text(sql)
    return stmt.bindparams(*(bindparam(name, type_=JSONB) for name in json_fields))


def rows(conn, sql, **params):
    return [dict(row) for row in conn.execute(text(sql), params).mappings()]


def row(conn, sql, **params):
    return next(iter(rows(conn, sql, **params)), None)


def execute(conn, sql, values, *json_fields):
    return conn.execute(query(sql, *json_fields), values)
