"""管理员入口：迁移与可重复演示数据。运行时应用不需要管理员凭据。"""
import hashlib
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from psycopg import sql

from .db import connect


ROOT = Path(__file__).resolve().parent.parent


def _body(path):
    lines = path.read_text().splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == "BEGIN;"]
    ends = [i for i, line in enumerate(lines) if line.strip() == "COMMIT;"]
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        raise RuntimeError(f"迁移事务边界不符合约定：{path.name}")
    # 外层由 Python 一并提交 DDL 和版本记录，文件仍可独立通过 psql 执行。
    del lines[ends[0]]
    del lines[starts[0]]
    return "\n".join(lines)


def _validate_legacy(conn, version):
    expected = {
        "app_user": {"user_id", "email", "display_name", "role", "status", "created_at", "updated_at"},
        "venue": {"venue_id", "name", "address", "status", "created_at", "updated_at"},
        "seat": {"seat_id", "venue_id", "seat_code", "row_label", "seat_number", "status", "created_at", "updated_at"},
        "activity": {"activity_id", "title", "description", "organizer_id", "status", "created_at", "updated_at"},
        "activity_session": {"session_id", "activity_id", "venue_id", "start_time", "end_time", "status", "created_at", "updated_at"},
    }
    constraints = {"fk_seat_venue", "uq_seat_venue_code", "uq_seat_venue_position", "fk_activity_organizer",
                   "fk_activity_session_activity", "fk_activity_session_venue", "ck_activity_session_time"}
    if version == "002_session_seat.sql":
        expected = {"session_seat": {"session_seat_id", "session_id", "seat_id", "venue_id", "price", "status", "created_at", "updated_at"}}
        constraints = {"uq_seat_id_venue", "uq_session_id_venue", "uq_session_seat",
                       "fk_session_seat_session_venue", "fk_session_seat_seat_venue", "ck_session_seat_price", "ck_session_seat_status"}
    for table, columns in expected.items():
        found = {row["column_name"] for row in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s", (table,),
        )}
        if found != columns:
            raise RuntimeError(f"旧表 {table} 的字段不符合已知版本，请人工检查，未自动登记")
        triggers = conn.execute(
            """SELECT count(*) AS value FROM pg_trigger WHERE tgrelid=%s::regclass
               AND tgname=%s AND NOT tgisinternal""", (f"public.{table}", f"trg_{table}_updated_at"),
        ).fetchone()["value"]
        if triggers != 1:
            raise RuntimeError(f"旧表 {table} 缺少修改时间触发器")
    found = {row["conname"] for row in conn.execute(
        "SELECT conname FROM pg_constraint WHERE connamespace='public'::regnamespace AND conname=ANY(%s)", (list(constraints),),
    )}
    if found != constraints:
        raise RuntimeError("旧结构缺少预期约束，停止登记")


def migrate(conn, directory=None):
    directory = directory or ROOT / "migrations"
    conn.execute("SELECT pg_advisory_lock(782314, 1)")
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS public.schema_migration (
            filename TEXT PRIMARY KEY, checksum TEXT NOT NULL,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            adopted_legacy BOOLEAN NOT NULL DEFAULT FALSE)""")
        for path in sorted(directory.glob("[0-9][0-9][0-9]_*.sql")):
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            recorded = conn.execute("SELECT checksum FROM schema_migration WHERE filename=%s", (path.name,)).fetchone()
            if recorded:
                if recorded["checksum"] != digest:
                    raise RuntimeError(f"已执行迁移被修改：{path.name}，请恢复原文件并新增迁移")
                continue
            sentinel = {"001_core_tables.sql": "app_user", "002_session_seat.sql": "session_seat"}.get(path.name)
            legacy = sentinel and conn.execute("SELECT to_regclass(%s) AS value", (f"public.{sentinel}",)).fetchone()["value"]
            with conn.transaction():
                if legacy:
                    _validate_legacy(conn, path.name)
                else:
                    conn.execute(_body(path), prepare=False)
                conn.execute("INSERT INTO schema_migration (filename, checksum, adopted_legacy) VALUES (%s,%s,%s)",
                             (path.name, digest, bool(legacy)))
            print(f"{'登记已有结构' if legacy else '应用迁移'}: {path.name}", flush=True)
    finally:
        conn.execute("SELECT pg_advisory_unlock(782314, 1)")


def grant_app(conn, *, password=None):
    role = conn.execute("SELECT rolsuper, rolcreatedb, rolcreaterole, shobj_description(oid, 'pg_authid') AS marker FROM pg_roles WHERE rolname='ticketing_app'").fetchone()
    marker = "ticketing-warehouse application role"
    if not role:
        if not password:
            raise RuntimeError("缺少应用数据库密码，无法创建 ticketing_app")
        conn.execute(sql.SQL("CREATE ROLE ticketing_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}").format(sql.Literal(password)))
        conn.execute(sql.SQL("COMMENT ON ROLE ticketing_app IS {}").format(sql.Literal(marker)))
    else:
        if role["rolsuper"] or role["rolcreatedb"] or role["rolcreaterole"] or role["marker"] != marker:
            raise RuntimeError("存在不属于本项目管理的 ticketing_app 账号，停止修改其权限或密码")
        if password:
            conn.execute(sql.SQL("ALTER ROLE ticketing_app PASSWORD {}").format(sql.Literal(password)))
    database = conn.execute("SELECT current_database() AS value").fetchone()["value"]
    conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO ticketing_app").format(sql.Identifier(database)))
    conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    conn.execute("GRANT USAGE ON SCHEMA public TO ticketing_app")
    conn.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO ticketing_app")
    conn.execute("GRANT INSERT ON booking_order, booking_item, order_status_history, payment_attempt, ticket, refund_attempt, app_user, auth_credential, auth_session TO ticketing_app")
    conn.execute("GRANT UPDATE ON booking_order, session_seat, ticket, auth_credential, auth_session TO ticketing_app")
    conn.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ticketing_app")


def initialize():
    with connect() as conn:
        migrate(conn)
        grant_app(conn, password=os.environ["TICKETING_APP_PASSWORD"])
    return {"status": "ready", "runtime_role": "ticketing_app"}


def seed_booking_demo(conn, *, start=None):
    """固定演示键复用场次；已过期则另建未来日期，历史记录不移动。"""
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(782314, 2)")
        now = conn.execute("SELECT clock_timestamp() AS value").fetchone()["value"]
        for email, name, role in (
            ("booking-organizer@example.test", "下单演示主办方", "ORGANIZER"),
            ("buyer-a@example.test", "演示用户 A", "CUSTOMER"),
            ("buyer-b@example.test", "演示用户 B", "CUSTOMER"),
        ):
            conn.execute("""INSERT INTO app_user (email, display_name, role) VALUES (%s,%s,%s)
                ON CONFLICT (lower(btrim(email))) DO NOTHING""", (email, name, role))
        users = conn.execute("SELECT user_id, email, role, status FROM app_user WHERE lower(btrim(email))=ANY(%s) ORDER BY email",
                             (["booking-organizer@example.test", "buyer-a@example.test", "buyer-b@example.test"],)).fetchall()
        by_email = {u["email"].strip().lower(): u for u in users}
        if any(u["status"] != "ACTIVE" for u in users) or by_email["booking-organizer@example.test"]["role"] not in ("ORGANIZER", "ADMIN"):
            raise RuntimeError("演示账号状态或主办权限不符，不自动覆盖")
        organizer = by_email["booking-organizer@example.test"]["user_id"]
        venues = conn.execute("SELECT venue_id, status FROM venue WHERE name='订单演示礼堂' AND address='教学演示专用'").fetchall()
        if len(venues) > 1:
            raise RuntimeError("订单演示场馆存在歧义")
        venue = venues[0] if venues else conn.execute("INSERT INTO venue (name,address) VALUES ('订单演示礼堂','教学演示专用') RETURNING venue_id,status").fetchone()
        if venue["status"] != "ACTIVE":
            raise RuntimeError("演示场馆已停用")
        venue_id = venue["venue_id"]
        for number in (1, 2):
            conn.execute("""INSERT INTO seat (venue_id,seat_code,row_label,seat_number) VALUES (%s,%s,'A',%s)
                ON CONFLICT (venue_id,seat_code) DO NOTHING""", (venue_id, f"A{number}", number))
        activities = conn.execute("SELECT activity_id,status FROM activity WHERE title='订单事务演示音乐会' AND organizer_id=%s", (organizer,)).fetchall()
        if len(activities) > 1:
            raise RuntimeError("订单演示活动存在歧义")
        activity = activities[0] if activities else conn.execute("""INSERT INTO activity (title,organizer_id,status)
            VALUES ('订单事务演示音乐会',%s,'PUBLISHED') RETURNING activity_id,status""", (organizer,)).fetchone()
        if activity["status"] != "PUBLISHED":
            raise RuntimeError("现有演示活动未发布，不自动修改")
        activity_id = activity["activity_id"]
        if start:
            start = datetime.fromisoformat(start)
            if start.tzinfo is None:
                raise RuntimeError("演示开始时间必须包含时区，例如 +08:00")
            if start <= now:
                raise RuntimeError("新演示日期必须在未来")
        else:
            existing = conn.execute("""SELECT start_time FROM activity_session WHERE activity_id=%s
                AND venue_id=%s AND start_time>%s AND status='ON_SALE' ORDER BY start_time LIMIT 1""",
                (activity_id, venue_id, now + timedelta(hours=1))).fetchone()
            start = existing["start_time"] if existing else (now + timedelta(days=7)).replace(hour=11, minute=0, second=0, microsecond=0)
        sessions = []
        for offset in (0, 1):
            when = start + timedelta(days=offset)
            row = conn.execute("""INSERT INTO activity_session (activity_id,venue_id,start_time,end_time,status)
                VALUES (%s,%s,%s,%s,'ON_SALE') ON CONFLICT (activity_id,venue_id,start_time) DO NOTHING
                RETURNING session_id,status,end_time""", (activity_id, venue_id, when, when + timedelta(hours=2))).fetchone()
            if not row:
                row = conn.execute("SELECT session_id,status,end_time FROM activity_session WHERE activity_id=%s AND venue_id=%s AND start_time=%s",
                                   (activity_id, venue_id, when)).fetchone()
            if row["status"] != "ON_SALE" or row["end_time"] != when + timedelta(hours=2):
                raise RuntimeError("已有演示场次状态或时间不符，不自动覆盖")
            sessions.append(row["session_id"])
            seats = conn.execute("SELECT seat_id,seat_code,row_label,seat_number,status FROM seat WHERE venue_id=%s AND seat_code IN ('A1','A2') ORDER BY seat_code", (venue_id,)).fetchall()
            if len(seats) != 2 or any(s["status"] != "ACTIVE" or s["row_label"] != "A" or s["seat_number"] != int(s["seat_code"][1:]) for s in seats):
                raise RuntimeError("现有演示座位不符合配置")
            for seat in seats:
                conn.execute("""INSERT INTO session_seat (session_id,seat_id,venue_id,price) VALUES (%s,%s,%s,%s)
                    ON CONFLICT (session_id,seat_id) DO NOTHING""",
                    (row["session_id"], seat["seat_id"], venue_id, 100 if seat["seat_code"] == "A1" else 120))
        return {"users": users, "session_ids": sessions, "note": "已保留原场次；重复运行不覆盖已有库存、价格或订单"}
