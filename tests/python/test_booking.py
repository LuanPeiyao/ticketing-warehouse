"""真实 PostgreSQL 集成测试。每轮创建独立临时数据库，不清理业务库。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
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

from ticketing.booking import BookingError, BookingService, RequestConflict, SeatUnavailable
from ticketing.bootstrap import ROOT, grant_app, migrate, seed_booking_demo
from ticketing.db import connect


class BookingIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database = 'ticketing_test_' + uuid4().hex
        cls.app_password = os.environ['TICKETING_APP_PASSWORD']
        with connect(dbname='postgres') as admin:
            admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(cls.database)))

        def cleanup():
            if not cls.database.startswith('ticketing_test_'):
                raise RuntimeError('拒绝清理非测试数据库')
            with connect(dbname='postgres') as admin:
                admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(cls.database)))
        cls.addClassCleanup(cleanup)
        with connect(dbname=cls.database) as admin:
            migrate(admin)
            grant_app(admin)

    def admin(self):
        return connect(dbname=self.database)

    def runtime(self, *, name='ticketing-test'):
        return connect(dbname=self.database, user='ticketing_app', password=self.app_password, application_name=name)

    def service(self, *, seconds=600):
        return BookingService(self.runtime, hold_seconds=seconds)

    def setUp(self):
        suffix = uuid4().hex
        with self.admin() as conn, conn.transaction():
            self.users = [conn.execute(
                "INSERT INTO app_user (email,display_name) VALUES (%s,%s) RETURNING user_id",
                (f'{suffix}-{n}@example.test', f'测试用户{n}'),
            ).fetchone()['user_id'] for n in (1, 2)]
            self.venue = conn.execute("INSERT INTO venue (name,address) VALUES ('事务测试场馆',%s) RETURNING venue_id", (suffix,)).fetchone()['venue_id']
            self.activity = conn.execute("INSERT INTO activity (title,organizer_id,status) VALUES ('事务测试活动',%s,'PUBLISHED') RETURNING activity_id", (self.users[0],)).fetchone()['activity_id']
            self.seats = [conn.execute("INSERT INTO seat (venue_id,seat_code,row_label,seat_number) VALUES (%s,%s,'A',%s) RETURNING seat_id",
                                     (self.venue, f'A{n}', n)).fetchone()['seat_id'] for n in (1, 2)]
            now = conn.execute('SELECT clock_timestamp() AS value').fetchone()['value']
            self.sessions = [conn.execute("""INSERT INTO activity_session (activity_id,venue_id,start_time,end_time,status)
                VALUES (%s,%s,%s,%s,'ON_SALE') RETURNING session_id""",
                (self.activity, self.venue, now + timedelta(days=d), now + timedelta(days=d, hours=2))).fetchone()['session_id'] for d in (7, 8)]
            self.inventory = []
            for session in self.sessions:
                self.inventory.append([conn.execute("INSERT INTO session_seat (session_id,seat_id,venue_id,price) VALUES (%s,%s,%s,%s) RETURNING session_seat_id",
                    (session, seat, self.venue, price)).fetchone()['session_seat_id'] for seat, price in zip(self.seats, (100, 120))])

    def reserve(self, *, user=0, seats=None, seconds=600, request=None):
        return self.service(seconds=seconds).reserve(self.users[user], self.sessions[0], self.inventory[0] if seats is None else seats, request_id=request)

    def value(self, statement, params=()):
        with self.admin() as conn:
            return conn.execute(statement, params).fetchone()['value']

    def inventory_row(self, inventory_id):
        with self.admin() as conn:
            return conn.execute('SELECT * FROM session_seat WHERE session_seat_id=%s', (inventory_id,)).fetchone()

    def order_count(self):
        return self.value('SELECT count(*) AS value FROM booking_order WHERE session_id=%s', (self.sessions[0],))

    def wait_expired(self, order):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.value('SELECT clock_timestamp()>%s AS value', (order['expires_at'],)):
                return
            time.sleep(0.02)
        self.fail('未在预期时间内到达过期时间')

    def test_normal_order_and_price_snapshot(self):
        order = self.reserve()
        self.assertEqual(order['status'], 'PENDING')
        self.assertEqual(order['total_amount'], Decimal('220.00'))
        self.assertEqual(len(order['items']), 2)
        for seat in self.inventory[0]:
            row = self.inventory_row(seat)
            self.assertEqual((row['status'], row['locked_order_id'], row['locked_until']), ('LOCKED', order['order_id'], order['expires_at']))
        # 当前价格修改不会改写已有订单中的价格快照。
        with self.admin() as conn:
            conn.execute('UPDATE session_seat SET price=999 WHERE session_seat_id=%s', (self.inventory[0][0],))
        result = self.service().get(self.users[0], order['order_id'])
        self.assertEqual(result['items'][0]['unit_price'], Decimal('100.00'))
        self.assertEqual(len(result['history']), 1)

    def test_idempotent_retry_and_conflicting_payload(self):
        request = uuid4()
        first = self.reserve(request=request)
        second = self.reserve(request=request, seats=list(reversed(self.inventory[0])))
        self.assertEqual(first['order_id'], second['order_id'])
        self.assertEqual(self.order_count(), 1)
        with self.assertRaises(RequestConflict):
            self.reserve(request=request, seats=[self.inventory[0][0]])

    def test_partial_unavailability_rolls_back_whole_order(self):
        occupied = self.reserve(user=1, seats=[self.inventory[0][1]])
        with self.assertRaises(SeatUnavailable):
            self.reserve()
        self.assertEqual(self.order_count(), 1)
        self.assertEqual(self.inventory_row(self.inventory[0][0])['status'], 'AVAILABLE')
        self.assertEqual(self.inventory_row(self.inventory[0][1])['locked_order_id'], occupied['order_id'])

    def test_injected_failure_rolls_back_order_items_and_locks(self):
        service = self.service()
        with patch.object(service, '_record_history', side_effect=RuntimeError('模拟创建末尾故障')):
            with self.assertRaises(RuntimeError):
                service.reserve(self.users[0], self.sessions[0], self.inventory[0])
        self.assertEqual(self.order_count(), 0)
        self.assertEqual(self.value('SELECT count(*) AS value FROM booking_item WHERE session_id=%s', (self.sessions[0],)), 0)
        self.assertTrue(all(self.inventory_row(i)['status'] == 'AVAILABLE' for i in self.inventory[0]))

    def _concurrent_reserve(self, *, same_request=False):
        suffix = uuid4().hex[:8]
        second_name = f'ticketing-racer-b-{suffix}'
        first_service = BookingService(partial(self.runtime, name=f'ticketing-racer-a-{suffix}'))
        second_service = BookingService(partial(self.runtime, name=second_name))
        first_holds = threading.Event()
        release_first = threading.Event()
        original_history = first_service._record_history

        def pause_after_lock(*args, **kwargs):
            original_history(*args, **kwargs)
            first_holds.set()
            if not release_first.wait(8):
                raise RuntimeError('并发验证等待超时')

        request = uuid4()
        targets = [self.inventory[0][0]]
        def call(service, user):
            try:
                return 'ok', service.reserve(user, self.sessions[0], targets, request_id=request if same_request else uuid4())
            except SeatUnavailable as exc:
                return 'unavailable', str(exc)

        with patch.object(first_service, '_record_history', new=pause_after_lock), ThreadPoolExecutor(max_workers=2) as pool:
            future_a = pool.submit(call, first_service, self.users[0])
            try:
                self.assertTrue(first_holds.wait(5), '第一个事务未取得库存锁')
                future_b = pool.submit(call, second_service, self.users[0] if same_request else self.users[1])
                deadline = time.monotonic() + 4
                evidence = None
                while time.monotonic() < deadline:
                    with self.admin() as conn:
                        row = conn.execute("""SELECT pid,wait_event_type,wait_event,pg_blocking_pids(pid) AS blockers
                            FROM pg_stat_activity WHERE application_name=%s AND wait_event_type='Lock'
                            AND cardinality(pg_blocking_pids(pid))>0""", (second_name,)).fetchone()
                    if row:
                        evidence = row
                        break
                    time.sleep(0.02)
                self.assertIsNotNone(evidence, '没有观察到第二个独立连接等待数据库锁')
                print('CONCURRENCY_EVIDENCE ' + json.dumps({'same_request': same_request, **evidence}, default=str), flush=True)
            finally:
                release_first.set()
            result_a, result_b = future_a.result(timeout=10), future_b.result(timeout=10)
        self.assertEqual(result_a[0], 'ok')
        if same_request:
            self.assertEqual(result_b[0], 'ok')
            self.assertEqual(result_a[1]['order_id'], result_b[1]['order_id'])
        else:
            self.assertEqual(result_b[0], 'unavailable')
        self.assertEqual(self.order_count(), 1)
        self.assertEqual(self.inventory_row(targets[0])['locked_order_id'], result_a[1]['order_id'])

    def test_two_connections_compete_for_same_seat(self):
        self._concurrent_reserve()

    def test_two_connections_retry_same_request(self):
        self._concurrent_reserve(same_request=True)

    def test_cancel_retry_does_not_release_new_owner(self):
        first = self.reserve(seats=[self.inventory[0][0]])
        cancelled = self.service().cancel(self.users[0], first['order_id'])
        self.assertEqual(cancelled['status'], 'CANCELLED')
        second = self.reserve(user=1, seats=[self.inventory[0][0]])
        self.service().cancel(self.users[0], first['order_id'])
        self.assertEqual(self.inventory_row(self.inventory[0][0])['locked_order_id'], second['order_id'])
        result = self.service().get(self.users[0], first['order_id'])
        self.assertEqual([r['to_status'] for r in result['history']], ['PENDING', 'CANCELLED'])

    def test_other_user_cannot_cancel_or_read_order(self):
        order = self.reserve()
        with self.assertRaises(BookingError):
            self.service().cancel(self.users[1], order['order_id'])
        with self.assertRaises(BookingError):
            self.service().get(self.users[1], order['order_id'])
        self.assertEqual(self.inventory_row(self.inventory[0][0])['status'], 'LOCKED')

    def test_expire_retry_and_rebook(self):
        order = self.reserve(seconds=1)
        self.wait_expired(order)
        self.assertIn(order['order_id'], self.service().expire())
        self.assertNotIn(order['order_id'], self.service().expire())
        self.assertEqual(self.service().get(self.users[0], order['order_id'])['status'], 'EXPIRED')
        new = self.reserve(user=1)
        self.assertNotEqual(order['order_id'], new['order_id'])

    def test_cleanup_preserves_unexpired_order(self):
        order = self.reserve()
        self.assertNotIn(order['order_id'], self.service().expire())
        self.assertEqual(self.inventory_row(self.inventory[0][0])['locked_order_id'], order['order_id'])

    def test_cancel_and_expire_race_reaches_one_terminal_state(self):
        order = self.reserve(seconds=1)
        self.wait_expired(order)
        barrier = threading.Barrier(2)
        def cancel():
            barrier.wait(timeout=5)
            return self.service().cancel(self.users[0], order['order_id'])
        def expire():
            barrier.wait(timeout=5)
            return self.service().expire()
        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = pool.submit(cancel), pool.submit(expire)
            a.result(timeout=10)
            b.result(timeout=10)
        result = self.service().get(self.users[0], order['order_id'])
        self.assertEqual(result['status'], 'EXPIRED')
        self.assertEqual([r['to_status'] for r in result['history']], ['PENDING', 'EXPIRED'])
        self.assertTrue(all(self.inventory_row(i)['status'] == 'AVAILABLE' for i in self.inventory[0]))

    def test_wrong_session_and_duplicate_selection(self):
        with self.assertRaises(SeatUnavailable):
            self.reserve(seats=[self.inventory[1][0]])
        with self.assertRaises(BookingError):
            self.reserve(seats=[self.inventory[0][0], self.inventory[0][0]])
        self.assertEqual(self.order_count(), 0)

    def test_sale_and_account_preconditions(self):
        changes = [
            ('app_user', 'user_id', self.users[0], 'status', 'INACTIVE', 'ACTIVE'),
            ('activity', 'activity_id', self.activity, 'status', 'DRAFT', 'PUBLISHED'),
            ('venue', 'venue_id', self.venue, 'status', 'INACTIVE', 'ACTIVE'),
            ('activity_session', 'session_id', self.sessions[0], 'status', 'SCHEDULED', 'ON_SALE'),
            ('seat', 'seat_id', self.seats[0], 'status', 'INACTIVE', 'ACTIVE'),
        ]
        for table, key, value, column, disabled, enabled in changes:
            with self.subTest(table=table):
                statement = sql.SQL('UPDATE {} SET {}=%s WHERE {}=%s').format(sql.Identifier(table), sql.Identifier(column), sql.Identifier(key))
                with self.admin() as conn:
                    conn.execute(statement, (disabled, value))
                try:
                    with self.assertRaises(BookingError):
                        self.reserve()
                finally:
                    with self.admin() as conn:
                        conn.execute(statement, (enabled, value))
        with self.admin() as conn:
            conn.execute("UPDATE activity_session SET start_time=clock_timestamp()-interval '2 hours', end_time=clock_timestamp()-interval '1 hour' WHERE session_id=%s", (self.sessions[0],))
        with self.assertRaises(BookingError):
            self.reserve()
        self.assertEqual(self.order_count(), 0)

    def test_database_rejects_orphan_locks_and_cross_session_items(self):
        with self.assertRaises(psycopg.errors.CheckViolation):
            with self.admin() as conn, conn.transaction():
                conn.execute("UPDATE session_seat SET status='LOCKED' WHERE session_seat_id=%s", (self.inventory[0][0],))
        order = self.reserve()
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            with self.admin() as conn, conn.transaction():
                conn.execute('INSERT INTO booking_item (order_id,session_id,session_seat_id,unit_price) VALUES (%s,%s,%s,100)',
                             (order['order_id'], self.sessions[0], self.inventory[1][0]))

    def test_runtime_role_cannot_create_or_delete_tables(self):
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with self.runtime() as conn:
                conn.execute('CREATE TABLE public.forbidden_test (id integer)')
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with self.runtime() as conn:
                conn.execute('DELETE FROM booking_order')

    def test_cli_reserve_cancel_and_worker_once(self):
        env = {**os.environ, 'PGDATABASE': self.database, 'PGUSER': 'ticketing_app', 'PGPASSWORD': self.app_password}
        def cli(*args):
            process = subprocess.run([sys.executable, '-m', 'ticketing', *map(str, args)], env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(process.returncode, 0, process.stderr)
            return json.loads(process.stdout)
        order = cli('reserve', '--user', self.users[0], '--session', self.sessions[0], '--seats', *self.inventory[0])
        self.assertEqual(cli('cancel', '--user', self.users[0], '--order', order['order_id'])['status'], 'CANCELLED')
        short = self.reserve(seconds=1)
        self.wait_expired(short)
        self.assertIn(short['order_id'], cli('worker', '--once')['expired_order_ids'])

    def test_migration_rerun_and_checksum_guard(self):
        with self.admin() as conn:
            migrate(conn)
            self.assertEqual(conn.execute('SELECT count(*) AS value FROM schema_migration').fetchone()['value'], 3)
            with tempfile.TemporaryDirectory() as directory:
                for path in (ROOT / 'migrations').glob('*.sql'):
                    (Path(directory) / path.name).write_bytes(path.read_bytes())
                changed = Path(directory) / '003_booking_orders.sql'
                changed.write_text(changed.read_text() + '\n-- changed\n')
                with self.assertRaisesRegex(RuntimeError, '已执行迁移被修改'):
                    migrate(conn, Path(directory))

    def test_demo_is_repeatable_and_preserves_stock(self):
        with self.admin() as conn:
            first = seed_booking_demo(conn)
        user = next(u['user_id'] for u in first['users'] if u['email'] == 'buyer-a@example.test')
        with self.admin() as conn:
            inventory = conn.execute('SELECT session_seat_id FROM session_seat WHERE session_id=%s ORDER BY session_seat_id', (first['session_ids'][0],)).fetchall()
            before = conn.execute('SELECT count(*) AS value FROM activity_session').fetchone()['value']
        order = self.service().reserve(user, first['session_ids'][0], [inventory[0]['session_seat_id']])
        with self.admin() as conn:
            second = seed_booking_demo(conn)
            after = conn.execute('SELECT count(*) AS value FROM activity_session').fetchone()['value']
        self.assertEqual(first['session_ids'], second['session_ids'])
        self.assertEqual(before, after)
        self.assertEqual(self.inventory_row(inventory[0]['session_seat_id'])['locked_order_id'], order['order_id'])
        self.service().cancel(user, order['order_id'])


class LegacyMigration(unittest.TestCase):
    def test_adopt_existing_001_002_without_rebuilding_data(self):
        database = 'ticketing_test_' + uuid4().hex
        with connect(dbname='postgres') as admin:
            admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
        try:
            with connect(dbname=database) as conn:
                for filename in ('001_core_tables.sql', '002_session_seat.sql'):
                    conn.execute((ROOT / 'migrations' / filename).read_text(), prepare=False)
                saved = conn.execute("INSERT INTO venue (name,address) VALUES ('保留验证','旧数据') RETURNING venue_id").fetchone()['venue_id']
                migrate(conn)
                self.assertEqual(conn.execute('SELECT count(*) AS value FROM schema_migration WHERE adopted_legacy').fetchone()['value'], 2)
                self.assertEqual(conn.execute('SELECT name FROM venue WHERE venue_id=%s', (saved,)).fetchone()['name'], '保留验证')
        finally:
            with connect(dbname='postgres') as admin:
                admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database)))


if __name__ == '__main__':
    unittest.main(verbosity=2)
