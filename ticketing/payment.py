"""本地同步模拟支付和整单退款；所有结果与业务状态在同一事务内提交。"""
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid4

from .booking import BookingError, BookingService, RequestConflict


class PaymentService(BookingService):
    @staticmethod
    def _request(value):
        try:
            return uuid4() if value is None else UUID(str(value))
        except (ValueError, TypeError, AttributeError) as exc:
            raise BookingError("request_id 必须是 UUID") from exc

    @staticmethod
    def _amount(value):
        try:
            # 金额使用字符串或 Decimal；拒绝二进制浮点数和布尔值。
            if not isinstance(value, (str, Decimal, int)) or isinstance(value, bool):
                raise ValueError
            amount = Decimal(value)
            if (not amount.is_finite() or not 0 <= amount < Decimal('1000000000000')
                    or amount != amount.quantize(Decimal('0.01'))):
                raise ValueError
            return amount
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise BookingError("金额必须是非负、最多两位小数的十进制数") from exc

    @staticmethod
    def _outcome(value):
        if value not in ('SUCCEEDED', 'FAILED'):
            raise BookingError("模拟结果必须为 SUCCEEDED 或 FAILED")
        return value

    @staticmethod
    def _lock_order(conn, user_id, order_id):
        if any(type(v) is not int or v <= 0 for v in (user_id, order_id)):
            raise BookingError("用户和订单编号必须为正整数")
        order = conn.execute('SELECT * FROM booking_order WHERE order_id=%s FOR UPDATE', (order_id,)).fetchone()
        if not order or order['user_id'] != user_id:
            raise BookingError("订单不存在或不属于该用户")
        return order

    @staticmethod
    def _inventory(conn, order, status):
        # 所有操作统一订单 -> 库存主键升序 -> 电子票的锁顺序。
        rows = conn.execute('''SELECT ss.* FROM session_seat ss JOIN booking_item i
            ON i.session_seat_id=ss.session_seat_id WHERE i.order_id=%s
            ORDER BY ss.session_seat_id FOR UPDATE OF ss''', (order['order_id'],)).fetchall()
        owner = 'locked_order_id' if status == 'LOCKED' else 'sold_order_id'
        if not rows or any(r['status'] != status or r[owner] != order['order_id'] for r in rows):
            raise BookingError("订单和库存归属不一致，已停止操作")
        if status == 'LOCKED' and any(r['locked_until'] != order['expires_at'] for r in rows):
            raise BookingError("订单和锁座截止时间不一致，已停止操作")
        return rows

    def _result(self, conn, user_id, order_id, key, attempt):
        return {key: attempt, 'order': self._details(conn, user_id, order_id)}

    def pay(self, user_id, order_id, amount, *, request_id=None, outcome='SUCCEEDED'):
        amount = self._amount(amount)
        request_id = self._request(request_id)
        outcome = self._outcome(outcome)
        with self.connect() as conn, conn.transaction():
            order = self._lock_order(conn, user_id, order_id)
            old = conn.execute('SELECT * FROM payment_attempt WHERE order_id=%s AND request_id=%s',
                               (order_id, request_id)).fetchone()
            if old:
                if old['amount'] != amount or old['status'] != outcome:
                    raise RequestConflict("同一支付 request_id 已用于不同金额或模拟结果")
                return self._result(conn, user_id, order_id, 'payment', old)
            if order['status'] != 'PENDING':
                raise BookingError("只有待支付订单可以支付；已成功的请求请复用原 request_id")
            if amount != order['total_amount']:
                raise BookingError("支付金额与订单价格快照总额不一致")
            self._inventory(conn, order, 'LOCKED')
            # 必须在等待订单及库存锁之后读实时时钟。
            now = conn.execute('SELECT clock_timestamp() AS value').fetchone()['value']
            if order['expires_at'] <= now:
                self._release(conn, order, 'EXPIRED', 'TIMEOUT', user_id)
                return self._result(conn, user_id, order_id, 'payment', None)
            attempt = conn.execute('''INSERT INTO payment_attempt
                (payment_no,order_id,request_id,amount,status) VALUES (%s,%s,%s,%s,%s) RETURNING *''',
                ('P' + uuid4().hex, order_id, request_id, amount, outcome)).fetchone()
            if outcome == 'SUCCEEDED':
                conn.execute("""UPDATE session_seat SET status='SOLD',sold_order_id=%s,
                    locked_order_id=NULL,locked_until=NULL WHERE locked_order_id=%s""", (order_id, order_id))
                items = conn.execute('SELECT item_id,session_seat_id FROM booking_item WHERE order_id=%s ORDER BY item_id',
                                     (order_id,)).fetchall()
                for item in items:
                    conn.execute('''INSERT INTO ticket (ticket_no,item_id,order_id,session_seat_id)
                        VALUES (%s,%s,%s,%s)''', (uuid4(), item['item_id'], order_id, item['session_seat_id']))
                # 在出票之后取时间，以便支付时间和所有票的签发时间顺序一致。
                conn.execute("UPDATE booking_order SET status='PAID',paid_at=clock_timestamp() WHERE order_id=%s", (order_id,))
                self._record_history(conn, order_id, 'PENDING', 'PAID', 'SIMULATED_PAYMENT', user_id)
            return self._result(conn, user_id, order_id, 'payment', attempt)

    def refund(self, user_id, order_id, *, reason, request_id=None, outcome='SUCCEEDED'):
        request_id = self._request(request_id)
        outcome = self._outcome(outcome)
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 200:
            raise BookingError("退款原因须为 1～200 字符")
        reason = reason.strip()
        with self.connect() as conn, conn.transaction():
            order = self._lock_order(conn, user_id, order_id)
            old = conn.execute('SELECT * FROM refund_attempt WHERE order_id=%s AND request_id=%s',
                               (order_id, request_id)).fetchone()
            if old:
                if old['reason'] != reason or old['status'] != outcome:
                    raise RequestConflict("同一退款 request_id 已用于不同原因或模拟结果")
                return self._result(conn, user_id, order_id, 'refund', old)
            if order['status'] != 'PAID':
                raise BookingError("只有已支付订单可以退款；已成功的请求请复用原 request_id")
            self._inventory(conn, order, 'SOLD')
            tickets = conn.execute('SELECT * FROM ticket WHERE order_id=%s ORDER BY ticket_id FOR UPDATE',
                                   (order_id,)).fetchall()
            count = conn.execute('SELECT count(*) AS value FROM booking_item WHERE order_id=%s', (order_id,)).fetchone()['value']
            if len(tickets) != count or any(t['status'] != 'VALID' for t in tickets):
                raise BookingError("订单电子票不完整或状态不一致，已停止退款")
            start = conn.execute('SELECT start_time FROM activity_session WHERE session_id=%s',
                                 (order['session_id'],)).fetchone()['start_time']
            now = conn.execute('SELECT clock_timestamp() AS value').fetchone()['value']
            if start <= now:
                raise BookingError("场次已经开始，当前规则不支持退款")
            payment = conn.execute("SELECT * FROM payment_attempt WHERE order_id=%s AND status='SUCCEEDED'",
                                   (order_id,)).fetchone()
            if not payment or payment['amount'] != order['total_amount']:
                raise BookingError("成功支付记录与订单金额不一致，已停止退款")
            attempt = conn.execute('''INSERT INTO refund_attempt
                (refund_no,payment_id,order_id,request_id,amount,reason,status)
                VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
                ('R' + uuid4().hex, payment['payment_id'], order_id, request_id,
                 payment['amount'], reason, outcome)).fetchone()
            if outcome == 'SUCCEEDED':
                conn.execute("UPDATE ticket SET status='REFUNDED',refunded_at=%s WHERE order_id=%s", (now, order_id))
                conn.execute("UPDATE session_seat SET status='AVAILABLE',sold_order_id=NULL WHERE sold_order_id=%s", (order_id,))
                conn.execute("UPDATE booking_order SET status='REFUNDED',refunded_at=%s WHERE order_id=%s", (now, order_id))
                self._record_history(conn, order_id, 'PAID', 'REFUNDED', 'SIMULATED_REFUND', user_id)
            return self._result(conn, user_id, order_id, 'refund', attempt)
