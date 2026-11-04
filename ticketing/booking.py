"""短事务锁座。数据库行锁仅保护写入，付款等待通过持久化状态表达。"""
from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from .db import connect


class BookingError(Exception):
    """可向调用方解释的业务失败。"""


class SeatUnavailable(BookingError):
    pass


class RequestConflict(BookingError):
    pass


ORDER_VIEW_SQL = """SELECT o.*,a.title AS activity_title,s.start_time AS session_start_time,v.name AS venue_name
    FROM booking_order o JOIN activity_session s ON s.session_id=o.session_id
    JOIN activity a ON a.activity_id=s.activity_id JOIN venue v ON v.venue_id=s.venue_id"""


class BookingService:
    def __init__(self, connect_factory=connect, *, hold_seconds=600):
        if not 1 <= hold_seconds <= 600:
            raise ValueError("锁座时间必须为 1～600 秒")
        self.connect = connect_factory
        self.hold_seconds = hold_seconds

    @staticmethod
    def _record_history(conn, order_id, old, new, reason, actor=None):
        conn.execute(
            """INSERT INTO order_status_history
               (order_id, from_status, to_status, reason, actor_user_id)
               VALUES (%s, %s, %s, %s, %s)""",
            (order_id, old, new, reason, actor),
        )

    @staticmethod
    def _order(conn, order_id):
        order = conn.execute(ORDER_VIEW_SQL + " WHERE o.order_id=%s FOR SHARE OF o", (order_id,)).fetchone()
        if not order:
            raise BookingError("订单不存在")
        order["items"] = conn.execute(
            """SELECT i.item_id,i.session_seat_id,i.unit_price,p.seat_code FROM booking_item i
               JOIN session_seat ss ON ss.session_seat_id=i.session_seat_id JOIN seat p ON p.seat_id=ss.seat_id
               WHERE i.order_id=%s ORDER BY i.session_seat_id""", (order_id,),
        ).fetchall()
        return order

    def _retry_result(self, conn, order, session_id, inventory_ids):
        result = self._order(conn, order["order_id"])
        saved = [item["session_seat_id"] for item in result["items"]]
        if order["session_id"] != session_id or saved != inventory_ids:
            raise RequestConflict("同一 request_id 已用于不同的场次或座位")
        return result

    def reserve(self, user_id, session_id, inventory_ids, *, request_id=None):
        if type(user_id) is not int or user_id <= 0 or type(session_id) is not int or session_id <= 0:
            raise BookingError("用户和场次编号必须为正整数")
        inventory_ids = list(inventory_ids)
        if (not 1 <= len(inventory_ids) <= 50
                or any(type(value) is not int or value <= 0 for value in inventory_ids)
                or len(set(inventory_ids)) != len(inventory_ids)):
            raise BookingError("每单须选择 1～50 个不重复的库存编号")
        inventory_ids.sort()
        try:
            request_id = UUID(str(request_id)) if request_id else uuid4()
        except (ValueError, TypeError) as exc:
            raise BookingError("request_id 必须是 UUID") from exc

        with self.connect() as conn, conn.transaction():
            old = conn.execute(
                "SELECT * FROM booking_order WHERE user_id=%s AND request_id=%s", (user_id, request_id),
            ).fetchone()
            if old:
                return self._retry_result(conn, old, session_id, inventory_ids)
            user = conn.execute("SELECT status FROM app_user WHERE user_id=%s", (user_id,)).fetchone()
            if not user or user["status"] != "ACTIVE":
                raise BookingError("用户不存在或已停用")
            session = conn.execute(
                """SELECT s.start_time, s.status, a.status AS activity_status, v.status AS venue_status
                   FROM activity_session s JOIN activity a USING (activity_id)
                   JOIN venue v ON v.venue_id=s.venue_id WHERE s.session_id=%s""", (session_id,),
            ).fetchone()
            if (not session or session["status"] != "ON_SALE"
                    or session["activity_status"] != "PUBLISHED" or session["venue_status"] != "ACTIVE"):
                raise BookingError("活动或场次尚未开售，或场馆已停用")
            now = conn.execute("SELECT clock_timestamp() AS value").fetchone()["value"]
            if session["start_time"] <= now:
                raise BookingError("场次已经开始，不能下单")
            expires = min(now + timedelta(seconds=self.hold_seconds), session["start_time"])
            # 先声明幂等键；并发相同请求在唯一约束处等待，不能生成两个订单。
            order = conn.execute(
                """INSERT INTO booking_order (order_no, user_id, session_id, request_id, expires_at)
                   VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT (user_id, request_id) DO NOTHING RETURNING *""",
                ("O" + uuid4().hex, user_id, session_id, request_id, expires),
            ).fetchone()
            if not order:
                old = conn.execute(
                    "SELECT * FROM booking_order WHERE user_id=%s AND request_id=%s", (user_id, request_id),
                ).fetchone()
                return self._retry_result(conn, old, session_id, inventory_ids)
            # 按库存主键排序加锁，所有多座位请求使用相同顺序。
            seats = conn.execute(
                """SELECT ss.session_seat_id, ss.price, ss.status, p.status AS physical_status
                   FROM session_seat ss JOIN seat p ON p.seat_id=ss.seat_id
                   WHERE ss.session_id=%s AND ss.session_seat_id=ANY(%s)
                   ORDER BY ss.session_seat_id FOR UPDATE OF ss""", (session_id, inventory_ids),
            ).fetchall()
            if (len(seats) != len(inventory_ids)
                    or any(row["status"] != "AVAILABLE" or row["physical_status"] != "ACTIVE" for row in seats)):
                raise SeatUnavailable("部分座位不存在、已停用或已被占用；本次下单全部回滚")
            # 等待行锁后重新取数据库时钟，避免用过时的时间计算截止点。
            now = conn.execute("SELECT clock_timestamp() AS value").fetchone()["value"]
            if session["start_time"] <= now:
                raise BookingError("等待座位期间场次已开始")
            expires = min(now + timedelta(seconds=self.hold_seconds), session["start_time"])
            amount = sum((row["price"] for row in seats), Decimal("0.00"))
            conn.execute("UPDATE booking_order SET total_amount=%s, expires_at=%s WHERE order_id=%s",
                         (amount, expires, order["order_id"]))
            with conn.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO booking_item (order_id, session_id, session_seat_id, unit_price)
                       VALUES (%s,%s,%s,%s)""",
                    [(order["order_id"], session_id, row["session_seat_id"], row["price"]) for row in seats],
                )
            conn.execute(
                """UPDATE session_seat SET status='LOCKED', locked_order_id=%s, locked_until=%s
                   WHERE session_seat_id=ANY(%s)""", (order["order_id"], expires, inventory_ids),
            )
            self._record_history(conn, order["order_id"], None, "PENDING", "CREATED", user_id)
            return self._order(conn, order["order_id"])

    def _release(self, conn, order, target, reason, actor=None):
        # 全部路径统一先锁订单，再按主键锁库存。只操作仍属于这个订单的库存。
        owned = conn.execute(
            """SELECT session_seat_id FROM session_seat WHERE locked_order_id=%s
               ORDER BY session_seat_id FOR UPDATE""", (order["order_id"],),
        ).fetchall()
        item_count = conn.execute(
            "SELECT count(*) AS value FROM booking_item WHERE order_id=%s", (order["order_id"],),
        ).fetchone()["value"]
        if not owned or len(owned) != item_count:
            raise BookingError("订单和锁座记录不一致，已停止释放，请检查数据")
        conn.execute(
            """UPDATE session_seat SET status='AVAILABLE', locked_order_id=NULL, locked_until=NULL
               WHERE locked_order_id=%s""", (order["order_id"],),
        )
        conn.execute("UPDATE booking_order SET status=%s WHERE order_id=%s", (target, order["order_id"]))
        self._record_history(conn, order["order_id"], "PENDING", target, reason, actor)

    def cancel(self, user_id, order_id):
        with self.connect() as conn, conn.transaction():
            order = conn.execute("SELECT * FROM booking_order WHERE order_id=%s FOR UPDATE", (order_id,)).fetchone()
            if not order or order["user_id"] != user_id:
                raise BookingError("订单不存在或不属于该用户")
            if order["status"] != "PENDING":
                return self._order(conn, order_id)
            now = conn.execute("SELECT clock_timestamp() AS value").fetchone()["value"]
            expired = order["expires_at"] <= now
            self._release(conn, order, "EXPIRED" if expired else "CANCELLED",
                          "TIMEOUT" if expired else "USER_CANCEL", user_id)
            return self._order(conn, order_id)

    def expire(self, *, limit=100):
        if not 1 <= limit <= 1000:
            raise BookingError("每批清理数量必须为 1～1000")
        with self.connect() as conn, conn.transaction():
            orders = conn.execute(
                """SELECT * FROM booking_order WHERE status='PENDING' AND expires_at<=clock_timestamp()
                   ORDER BY order_id LIMIT %s FOR UPDATE SKIP LOCKED""", (limit,),
            ).fetchall()
            for order in orders:
                self._release(conn, order, "EXPIRED", "TIMEOUT")
            return [order["order_id"] for order in orders]

    def _details(self, conn, user_id, order_id):
        order = self._order(conn, order_id)
        if order["user_id"] != user_id:
            raise BookingError("订单不属于该用户")
        order["history"] = conn.execute(
            """SELECT from_status, to_status, reason, changed_at FROM order_status_history
               WHERE order_id=%s ORDER BY history_id""", (order_id,),
        ).fetchall()
        order["payments"] = conn.execute(
            "SELECT * FROM payment_attempt WHERE order_id=%s ORDER BY payment_id", (order_id,),
        ).fetchall()
        order["tickets"] = conn.execute(
            "SELECT * FROM ticket WHERE order_id=%s ORDER BY ticket_id", (order_id,),
        ).fetchall()
        order["refunds"] = conn.execute(
            "SELECT * FROM refund_attempt WHERE order_id=%s ORDER BY refund_id", (order_id,),
        ).fetchall()
        return order

    def get(self, user_id, order_id):
        with self.connect() as conn, conn.transaction():
            return self._details(conn, user_id, order_id)
