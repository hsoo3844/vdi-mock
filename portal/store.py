"""PostgreSQL store for users and audit events.

Mock auth: 이름만으로 로그인, 처음 보는 이름은 일반 유저로 자동 등록.
Desktops are not stored here: their source of truth is the backend (Pod labels
in the mock, Nova in the real environment).
"""
import os
import time

import psycopg
from psycopg.rows import dict_row

DSN = (
    f"host={os.environ.get('DB_HOST', 'postgres')} dbname={os.environ.get('DB_NAME', 'vdi')} "
    f"user={os.environ.get('DB_USER', 'vdi')} password={os.environ.get('DB_PASSWORD', '')}"
)
DEFAULT_QUOTA = int(os.environ.get("VDI_QUOTA_PER_USER", "2"))
ADMIN_USERNAME = os.environ.get("VDI_ADMIN_USERNAME", "admin")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username   text PRIMARY KEY,
    role       text NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
    quota      int  NOT NULL DEFAULT 2 CHECK (quota >= 0),
    disabled   boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_login timestamptz
);
CREATE TABLE IF NOT EXISTS events (
    id     bigserial PRIMARY KEY,
    at     timestamptz NOT NULL DEFAULT now(),
    actor  text NOT NULL,
    action text NOT NULL,
    target text,
    detail text
);
"""


def _conn():
    return psycopg.connect(DSN, row_factory=dict_row, autocommit=True)


def init():
    for attempt in range(30):  # DB가 늦게 뜨는 경우 대기
        try:
            with _conn() as c:
                c.execute(SCHEMA)
                c.execute(
                    "INSERT INTO users (username, role, quota) VALUES (%s, 'admin', 10)"
                    " ON CONFLICT (username) DO UPDATE SET role = 'admin', disabled = false",
                    (ADMIN_USERNAME,),
                )
            return
        except psycopg.OperationalError:
            if attempt == 29:
                raise
            time.sleep(2)


def login(username):
    """Returns the user (auto-registered on first login), or None if disabled."""
    with _conn() as c:
        c.execute(
            "INSERT INTO users (username, quota) VALUES (%s, %s) ON CONFLICT (username) DO NOTHING",
            (username, DEFAULT_QUOTA),
        )
        return c.execute(
            "UPDATE users SET last_login = now() WHERE username = %s AND NOT disabled RETURNING *",
            (username,),
        ).fetchone()


def get_user(username):
    with _conn() as c:
        return c.execute("SELECT * FROM users WHERE username = %s", (username,)).fetchone()


def list_users():
    with _conn() as c:
        return c.execute("SELECT * FROM users ORDER BY created_at").fetchall()


def create_user(username, role="user", quota=None):
    with _conn() as c:
        return c.execute(
            "INSERT INTO users (username, role, quota) VALUES (%s, %s, %s)"
            " ON CONFLICT (username) DO NOTHING RETURNING *",
            (username, role, DEFAULT_QUOTA if quota is None else quota),
        ).fetchone()


def update_user(username, role=None, quota=None, disabled=None):
    fields = {k: v for k, v in {"role": role, "quota": quota, "disabled": disabled}.items() if v is not None}
    if not fields:
        return get_user(username)
    with _conn() as c:
        return c.execute(
            f"UPDATE users SET {', '.join(f'{k} = %s' for k in fields)} WHERE username = %s RETURNING *",
            [*fields.values(), username],
        ).fetchone()


def delete_user(username):
    with _conn() as c:
        c.execute("DELETE FROM users WHERE username = %s", (username,))


def count_active_admins():
    with _conn() as c:
        return c.execute("SELECT count(*) AS n FROM users WHERE role = 'admin' AND NOT disabled").fetchone()["n"]


def log(actor, action, target=None, detail=None):
    with _conn() as c:
        c.execute(
            "INSERT INTO events (actor, action, target, detail) VALUES (%s, %s, %s, %s)",
            (actor, action, target, detail),
        )


def events(limit=100, username=None):
    with _conn() as c:
        if username:
            return c.execute(
                "SELECT * FROM events WHERE actor = %s OR target = %s ORDER BY id DESC LIMIT %s",
                (username, username, limit),
            ).fetchall()
        return c.execute("SELECT * FROM events ORDER BY id DESC LIMIT %s", (limit,)).fetchall()
