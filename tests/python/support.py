"""独立临时数据库和测试数据夹具。"""
from datetime import timedelta
import os
import time
import unittest
from uuid import uuid4

from psycopg import sql

from ticketing.booking import BookingService
from ticketing.bootstrap import grant_app, migrate
from ticketing.db import connect


class BookingFixture(unittest.TestCase):
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
            grant_app(admin, password=cls.app_password)

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
