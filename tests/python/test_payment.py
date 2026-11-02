"""支付、出票、退款与真实 PostgreSQL 并发测试。"""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from functools import partial
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg import sql

from support import BookingFixture
from ticketing.booking import BookingError, RequestConflict, SeatUnavailable
from ticketing.bootstrap import ROOT, grant_app, migrate, seed_booking_demo
from ticketing.db import connect
from ticketing.payment import PaymentService


class PaymentIntegration(BookingFixture):
    def payments(self):
        return PaymentService(self.runtime)

    def pay(self, order, **kwargs):
        return self.payments().pay(self.users[0], order['order_id'], order['total_amount'], **kwargs)

    def refund(self, order, **kwargs):
        return self.payments().refund(self.users[0], order['order_id'], reason='行程改变', **kwargs)

    def assert_stock(self, status, owner=None):
        for inventory in self.inventory[0]:
            row = self.inventory_row(inventory)
            self.assertEqual(row['status'], status)
            self.assertEqual(row['sold_order_id'], owner)
            if status != 'LOCKED':
                self.assertIsNone(row['locked_order_id'])
                self.assertIsNone(row['locked_until'])

    def test_success_issues_one_ticket_per_item_and_preserves_price(self):
        order = self.reserve()
        with self.admin() as conn:
            conn.execute('UPDATE session_seat SET price=999 WHERE session_id=%s', (self.sessions[0],))
        result = self.pay(order)
        self.assertEqual(result['payment']['amount'], Decimal('220.00'))
        self.assertEqual(result['payment']['provider'], 'SIMULATED')
        self.assertEqual(result['order']['status'], 'PAID')
        self.assertIsNotNone(result['order']['paid_at'])
        self.assertEqual(len(result['order']['tickets']), 2)
        self.assertEqual({t['status'] for t in result['order']['tickets']}, {'VALID'})
        self.assertEqual([h['to_status'] for h in result['order']['history']], ['PENDING', 'PAID'])
        self.assert_stock('SOLD', order['order_id'])
        with self.assertRaises(SeatUnavailable):
            self.reserve(user=1)
        self.assertEqual(self.service().cancel(self.users[0], order['order_id'])['status'], 'PAID')
        self.assertNotIn(order['order_id'], self.service().expire())

    def test_payment_failure_retry_and_request_payload_guard(self):
        order = self.reserve()
        request = uuid4()
        failed = self.pay(order, request_id=request, outcome='FAILED')
        repeated = self.pay(order, request_id=request, outcome='FAILED')
        self.assertEqual(failed['payment']['payment_id'], repeated['payment']['payment_id'])
        self.assertEqual(failed['order']['status'], 'PENDING')
        self.assertEqual(failed['order']['tickets'], [])
        self.assert_stock('LOCKED')
        with self.assertRaises(RequestConflict):
            self.pay(order, request_id=request)
        success = self.pay(order)
        self.assertEqual(len(success['order']['payments']), 2)
        self.assertEqual(success['order']['status'], 'PAID')

    def test_payment_idempotency_and_new_request_after_success(self):
        order = self.reserve()
        request = uuid4()
        first = self.pay(order, request_id=request)
        second = self.pay(order, request_id=request)
        self.assertEqual(first, second)
        with self.assertRaises(BookingError):
            self.pay(order)
        with self.assertRaises(RequestConflict):
            self.payments().pay(self.users[0], order['order_id'], '219', request_id=request)
        self.assertEqual(len(second['order']['payments']), 1)

    def test_reject_wrong_amount_invalid_inputs_and_unauthorized_user(self):
        order = self.reserve()
        for amount in ('219.99', '-1', 'NaN', 'Infinity', '1.001', True, 220.0, '1e999999'):
            with self.subTest(amount=amount), self.assertRaises(BookingError):
                self.payments().pay(self.users[0], order['order_id'], amount)
        with self.assertRaises(BookingError):
            self.payments().pay(self.users[1], order['order_id'], '220')
        for request in ('', 'bad-uuid'):
            with self.assertRaises(BookingError):
                self.pay(order, request_id=request)
        with self.assertRaises(BookingError):
            self.pay(order, outcome='INVALID')
        self.assertEqual(self.value('SELECT count(*) AS value FROM payment_attempt WHERE order_id=%s', (order['order_id'],)), 0)
        self.assert_stock('LOCKED')

    def test_expired_payment_commits_expiry_and_never_issues_tickets(self):
        order = self.reserve(seconds=1)
        self.wait_expired(order)
        result = self.pay(order)
        self.assertIsNone(result['payment'])
        self.assertEqual(result['order']['status'], 'EXPIRED')
        self.assertEqual(result['order']['tickets'], [])
        self.assertEqual(result['order']['payments'], [])
        self.assert_stock('AVAILABLE')
        new = self.reserve(user=1)
        with self.assertRaises(BookingError):
            self.pay(order)
        self.assertEqual(self.inventory_row(self.inventory[0][0])['locked_order_id'], new['order_id'])

    def test_cancelled_order_cannot_pay_or_refund(self):
        order = self.reserve()
        with self.assertRaises(BookingError):
            self.refund(order)
        self.service().cancel(self.users[0], order['order_id'])
        with self.assertRaises(BookingError):
            self.pay(order)
        with self.assertRaises(BookingError):
            self.refund(order)

    def test_payment_failure_at_commit_boundary_rolls_back_all_writes(self):
        order = self.reserve()
        service = self.payments()
        with patch.object(service, '_record_history', side_effect=RuntimeError('模拟出票后故障')):
            with self.assertRaises(RuntimeError):
                service.pay(self.users[0], order['order_id'], '220')
        result = self.service().get(self.users[0], order['order_id'])
        self.assertEqual(result['status'], 'PENDING')
        self.assertIsNone(result['paid_at'])
        self.assertEqual(result['payments'], [])
        self.assertEqual(result['tickets'], [])
        self.assert_stock('LOCKED')

    def test_full_refund_voids_tickets_and_allows_resale(self):
        order = self.reserve()
        payment_request, refund_request = uuid4(), uuid4()
        paid = self.pay(order, request_id=payment_request)
        result = self.refund(order, request_id=refund_request)
        self.assertEqual(result['refund']['amount'], paid['payment']['amount'])
        self.assertEqual(result['refund']['payment_id'], paid['payment']['payment_id'])
        self.assertEqual(result['order']['status'], 'REFUNDED')
        self.assertTrue(all(t['status'] == 'REFUNDED' and t['refunded_at'] for t in result['order']['tickets']))
        self.assertEqual([h['to_status'] for h in result['order']['history']], ['PENDING', 'PAID', 'REFUNDED'])
        self.assert_stock('AVAILABLE')
        new = self.reserve(user=1)
        new_paid = self.payments().pay(self.users[1], new['order_id'], '220')
        # 重放旧支付和旧退款，只返回旧记录，不会释放后来已售的库存。
        self.assertEqual(self.pay(order, request_id=payment_request)['order']['status'], 'REFUNDED')
        self.assertEqual(self.refund(order, request_id=refund_request)['refund']['refund_id'], result['refund']['refund_id'])
        self.assertEqual(len(new_paid['order']['tickets']), 2)
        self.assert_stock('SOLD', new['order_id'])
        self.assertEqual(self.value("SELECT count(*) AS value FROM ticket WHERE session_seat_id=ANY(%s) AND status='VALID'", (self.inventory[0],)), 2)

    def test_refund_failure_retry_and_conflicting_payload(self):
        order = self.reserve()
        self.pay(order)
        request = uuid4()
        first = self.refund(order, request_id=request, outcome='FAILED')
        again = self.refund(order, request_id=request, outcome='FAILED')
        self.assertEqual(first, again)
        self.assertEqual(first['order']['status'], 'PAID')
        self.assert_stock('SOLD', order['order_id'])
        self.assertTrue(all(t['status'] == 'VALID' for t in first['order']['tickets']))
        with self.assertRaises(RequestConflict):
            self.refund(order, request_id=request)
        success_request = uuid4()
        result = self.refund(order, request_id=success_request)
        self.assertEqual(len(result['order']['refunds']), 2)
        with self.assertRaises(RequestConflict):
            self.payments().refund(self.users[0], order['order_id'], reason='不同原因', request_id=success_request)
        with self.assertRaises(BookingError):
            self.refund(order)

    def test_refund_validates_owner_reason_and_start_time(self):
        order = self.reserve()
        self.pay(order)
        with self.assertRaises(BookingError):
            self.payments().refund(self.users[1], order['order_id'], reason='非本人')
        for reason in ('', '   ', 'a' * 201, None):
            with self.assertRaises(BookingError):
                self.payments().refund(self.users[0], order['order_id'], reason=reason)
        with self.admin() as conn:
            conn.execute("UPDATE activity_session SET start_time=clock_timestamp()-interval '1 minute',end_time=clock_timestamp()+interval '1 hour' WHERE session_id=%s", (self.sessions[0],))
        with self.assertRaises(BookingError):
            self.refund(order)
        result = self.service().get(self.users[0], order['order_id'])
        self.assertEqual(result['refunds'], [])
        self.assertEqual(result['status'], 'PAID')

    def test_refund_failure_at_commit_boundary_rolls_back_all_writes(self):
        order = self.reserve()
        self.pay(order)
        service = self.payments()
        with patch.object(service, '_record_history', side_effect=RuntimeError('模拟退款末尾故障')):
            with self.assertRaises(RuntimeError):
                service.refund(self.users[0], order['order_id'], reason='测试')
        result = self.service().get(self.users[0], order['order_id'])
        self.assertEqual(result['status'], 'PAID')
        self.assertIsNone(result['refunded_at'])
        self.assertEqual(result['refunds'], [])
        self.assertTrue(all(t['status'] == 'VALID' for t in result['tickets']))
        self.assert_stock('SOLD', order['order_id'])

    def _overlap(self, first_service, action_a, action_b, *, wait=None):
        """暂停 A 的事务末尾，让 B 确实在数据库行锁上等待。"""
        held, release = threading.Event(), threading.Event()
        original = first_service._record_history
        name = 'payment-racer-' + uuid4().hex[:10]
        second_service = PaymentService(partial(self.runtime, name=name))
        def paused(*args, **kwargs):
            original(*args, **kwargs)
            held.set()
            if not release.wait(8):
                raise RuntimeError('并发验证等待超时')
        with patch.object(first_service, '_record_history', side_effect=paused), ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(action_a, first_service)
            try:
                self.assertTrue(held.wait(5))
                b = pool.submit(action_b, second_service)
                deadline = time.monotonic() + 4
                evidence = None
                while time.monotonic() < deadline:
                    with self.admin() as conn:
                        evidence = conn.execute('''SELECT pid,wait_event_type,wait_event,pg_blocking_pids(pid) AS blockers
                            FROM pg_stat_activity WHERE application_name=%s AND wait_event_type='Lock'
                            AND cardinality(pg_blocking_pids(pid))>0''', (name,)).fetchone()
                    if evidence:
                        break
                    time.sleep(0.02)
                self.assertIsNotNone(evidence)
                print('PAYMENT_CONCURRENCY_EVIDENCE ' + json.dumps(evidence), flush=True)
                if wait:
                    wait()
            finally:
                release.set()
            return a.result(timeout=10), b.result(timeout=10)

    def test_concurrent_identical_payment_only_pays_and_issues_once(self):
        order, request = self.reserve(), uuid4()
        def pay(service):
            return service.pay(self.users[0], order['order_id'], '220', request_id=request)
        a, b = self._overlap(self.payments(), pay, pay)
        self.assertEqual(a, b)
        self.assertEqual(len(b['order']['payments']), 1)
        self.assertEqual(len(b['order']['tickets']), 2)

    def test_concurrent_identical_refund_only_refunds_once(self):
        order, request = self.reserve(), uuid4()
        self.pay(order)
        def refund(service):
            return service.refund(self.users[0], order['order_id'], reason='并发退款', request_id=request)
        a, b = self._overlap(self.payments(), refund, refund)
        self.assertEqual(a, b)
        self.assertEqual(len(b['order']['refunds']), 1)
        self.assert_stock('AVAILABLE')

    def test_concurrent_refund_and_repurchase_wait_for_inventory(self):
        order = self.reserve()
        self.pay(order)
        def refund(service):
            return service.refund(self.users[0], order['order_id'], reason='并发重售')
        def reserve(service):
            return service.reserve(self.users[1], self.sessions[0], self.inventory[0])
        a, b = self._overlap(self.payments(), refund, reserve)
        self.assertEqual(a['order']['status'], 'REFUNDED')
        self.assertEqual(b['status'], 'PENDING')
        self.assertTrue(all(self.inventory_row(i)['locked_order_id'] == b['order_id'] for i in self.inventory[0]))

    def test_concurrent_payment_and_cancel_keep_paid_seats(self):
        order = self.reserve()
        def pay(service):
            return service.pay(self.users[0], order['order_id'], '220')
        def cancel(service):
            return service.cancel(self.users[0], order['order_id'])
        a, b = self._overlap(self.payments(), pay, cancel)
        self.assertEqual(a['order']['status'], 'PAID')
        self.assertEqual(b['status'], 'PAID')
        self.assert_stock('SOLD', order['order_id'])

    def test_payment_waiting_for_order_lock_rechecks_expiry(self):
        order = self.reserve(seconds=1)
        # A 创建订单的幂等重试没有状态写入，改用持有订单行锁的独立连接。
        name = 'payment-expiry-' + uuid4().hex[:8]
        service = PaymentService(partial(self.runtime, name=name))
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.admin() as conn, conn.transaction():
                conn.execute('SELECT order_id FROM booking_order WHERE order_id=%s FOR UPDATE', (order['order_id'],))
                future = pool.submit(service.pay, self.users[0], order['order_id'], '220')
                deadline = time.monotonic() + 4
                evidence = None
                while time.monotonic() < deadline:
                    with self.admin() as observer:
                        evidence = observer.execute("SELECT pid,pg_blocking_pids(pid) AS blockers FROM pg_stat_activity WHERE application_name=%s AND wait_event_type='Lock'", (name,)).fetchone()
                    if evidence:
                        break
                    time.sleep(.02)
                self.assertIsNotNone(evidence)
                self.wait_expired(order)
            result = future.result(timeout=10)
        self.assertEqual(result['order']['status'], 'EXPIRED')
        self.assertIsNone(result['payment'])
        self.assert_stock('AVAILABLE')

    def test_payment_commits_before_expiry_worker_cannot_release_paid_stock(self):
        order = self.reserve(seconds=1)
        service = self.payments()
        held, release = threading.Event(), threading.Event()
        original = service._record_history
        def pause(*args, **kwargs):
            original(*args, **kwargs)
            held.set()
            if not release.wait(8):
                raise RuntimeError('等待超时')
        with patch.object(service, '_record_history', side_effect=pause), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(service.pay, self.users[0], order['order_id'], '220')
            try:
                self.assertTrue(held.wait(5))
                self.wait_expired(order)
                # Worker 使用 SKIP LOCKED，跳过正在支付中的订单。
                self.assertNotIn(order['order_id'], self.service().expire())
            finally:
                release.set()
            self.assertEqual(future.result(timeout=10)['order']['status'], 'PAID')
        self.assertNotIn(order['order_id'], self.service().expire())
        self.assert_stock('SOLD', order['order_id'])

    def test_expiry_wins_against_payment(self):
        order = self.reserve(seconds=1)
        self.wait_expired(order)
        def expire(service):
            return service.expire()
        def pay(service):
            try:
                service.pay(self.users[0], order['order_id'], '220')
            except BookingError:
                return 'rejected'
            return 'unexpected'
        a, b = self._overlap(self.payments(), expire, pay)
        self.assertIn(order['order_id'], a)
        self.assertEqual(b, 'rejected')
        self.assert_stock('AVAILABLE')

    def test_constraints_and_append_only_attempt_permissions(self):
        order = self.reserve()
        paid = self.pay(order)
        with self.assertRaises(psycopg.errors.UniqueViolation):
            with self.admin() as conn:
                conn.execute("INSERT INTO payment_attempt(payment_no,order_id,request_id,amount,status) VALUES (%s,%s,%s,220,'SUCCEEDED')",
                             ('P' + uuid4().hex, order['order_id'], uuid4()))
        with self.assertRaises(psycopg.errors.CheckViolation):
            with self.admin() as conn:
                conn.execute("UPDATE session_seat SET sold_order_id=NULL WHERE session_seat_id=%s", (self.inventory[0][0],))
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            with self.admin() as conn:
                conn.execute("INSERT INTO refund_attempt(refund_no,payment_id,order_id,request_id,amount,reason,status) VALUES (%s,%s,%s,%s,1,'错误金额','SUCCEEDED')",
                             ('R' + uuid4().hex, paid['payment']['payment_id'], order['order_id'], uuid4()))
        for statement in ('UPDATE payment_attempt SET amount=1', 'DELETE FROM refund_attempt', 'DELETE FROM ticket'):
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with self.runtime() as conn:
                    conn.execute(statement)

    def test_cli_payment_failure_success_and_refund(self):
        order = self.reserve()
        env = {**os.environ, 'PGDATABASE': self.database, 'PGUSER': 'ticketing_app', 'PGPASSWORD': self.app_password}
        def cli(*args):
            process = subprocess.run([sys.executable, '-m', 'ticketing', *map(str, args)], env=env,
                                     capture_output=True, text=True, timeout=20)
            self.assertEqual(process.returncode, 0, process.stderr)
            return json.loads(process.stdout)
        common = ('--user', self.users[0], '--order', order['order_id'])
        failed = cli('pay', *common, '--amount', '220', '--outcome', 'FAILED')
        self.assertEqual(failed['payment']['status'], 'FAILED')
        paid = cli('pay', *common, '--amount', '220')
        self.assertEqual(paid['order']['status'], 'PAID')
        result = cli('refund', *common, '--reason', 'CLI演示')
        self.assertEqual(result['order']['status'], 'REFUNDED')
        self.assertEqual(cli('order', *common)['tickets'][0]['status'], 'REFUNDED')


class PaymentUpgrade(unittest.TestCase):
    def test_upgrade_003_preserves_pending_order_then_can_pay_and_refund(self):
        database = 'ticketing_test_' + uuid4().hex
        with connect(dbname='postgres') as conn:
            conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
        try:
            factory = partial(connect, dbname=database)
            with tempfile.TemporaryDirectory() as directory, factory() as conn:
                for filename in ('001_core_tables.sql', '002_session_seat.sql', '003_booking_orders.sql'):
                    (Path(directory) / filename).write_bytes((ROOT / 'migrations' / filename).read_bytes())
                migrate(conn, Path(directory))
                demo = seed_booking_demo(conn)
                user = next(u['user_id'] for u in demo['users'] if u['email'] == 'buyer-a@example.test')
                session = demo['session_ids'][0]
                seat = conn.execute('SELECT session_seat_id FROM session_seat WHERE session_id=%s ORDER BY session_seat_id LIMIT 1', (session,)).fetchone()['session_seat_id']
                order = PaymentService(factory).reserve(user, session, [seat])
                migrate(conn)
                grant_app(conn)
                runtime = partial(connect, dbname=database, user='ticketing_app', password=os.environ['TICKETING_APP_PASSWORD'])
                service = PaymentService(runtime)
                saved = service.get(user, order['order_id'])
                self.assertEqual(saved['items'], order['items'])
                self.assertEqual(saved['expires_at'], order['expires_at'])
                self.assertEqual(saved['status'], 'PENDING')
                self.assertEqual(service.pay(user, order['order_id'], order['total_amount'])['order']['status'], 'PAID')
                self.assertEqual(service.refund(user, order['order_id'], reason='升级验证')['order']['status'], 'REFUNDED')
        finally:
            with connect(dbname='postgres') as conn:
                conn.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database)))
