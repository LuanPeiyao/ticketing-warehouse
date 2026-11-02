"""真实 PostgreSQL + FastAPI TestClient：验证 HTTP 契约、认证与订单隔离。"""
from concurrent.futures import ThreadPoolExecutor
import json
import secrets
import threading
from uuid import uuid4

from fastapi.testclient import TestClient
import psycopg

from support import BookingFixture
from ticketing.api import create_app
from ticketing.auth import AuthService
from ticketing.bootstrap import grant_app, migrate


class APIIntegration(BookingFixture):
    def setUp(self):
        super().setUp()
        self.app = create_app(self.runtime)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.email = 'api-' + uuid4().hex + '@example.com'
        self.password = secrets.token_urlsafe(16)

    def register(self, *, email=None, password=None):
        result = self.client.post('/auth/register', json={
            'email': email or self.email, 'password': password or self.password, 'display_name': '接口测试用户'})
        self.assertEqual(result.status_code, 201, result.text)
        return result.json()

    def login(self, *, email=None, password=None):
        result = self.client.post('/auth/login', json={'email': email or self.email, 'password': password or self.password})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def headers(self, token=None):
        return {'Authorization': 'Bearer ' + (token or self.login()['access_token'])}

    def reserve_api(self, *, headers=None, request=None, seats=None):
        result = self.client.post('/orders', headers=headers or self.headers(), json={
            'session_id': self.sessions[0], 'inventory_ids': self.inventory[0] if seats is None else seats,
            'request_id': str(request or uuid4())})
        self.assertEqual(result.status_code, 201, result.text)
        self.assertEqual(result.headers['location'], '/orders/' + str(result.json()['order_id']))
        return result.json()

    def test_register_hashes_password_and_normalizes_email(self):
        user = self.register(email='  '+self.email.upper()+'  ')
        self.assertEqual(user['email'], self.email)
        self.assertEqual(user['role'], 'CUSTOMER')
        self.assertNotIn(self.password, json.dumps(user))
        with self.admin() as conn:
            row = conn.execute('SELECT password_hash FROM auth_credential WHERE user_id=%s', (user['user_id'],)).fetchone()
        self.assertTrue(row['password_hash'].startswith('$argon2id$'))
        self.assertNotIn(self.password, row['password_hash'])
        duplicate = self.client.post('/auth/register', json={'email': self.email, 'password': self.password, 'display_name': '重复'})
        self.assertEqual(duplicate.status_code, 409)
        with self.admin() as conn:
            conn.execute("INSERT INTO app_user (email,display_name) VALUES (%s,'旧用户')", ('old-'+self.email,))
        takeover = self.client.post('/auth/register', json={'email': 'old-'+self.email, 'password': self.password, 'display_name': '尝试接管'})
        self.assertEqual(takeover.status_code, 409)

    def test_registration_rejects_role_injection_weak_password_and_bad_email(self):
        good = {'email': self.email, 'password': self.password, 'display_name': '接口用户'}
        for change in ({'role': 'ADMIN'}, {'user_id': 1}, {'password': 'short'}, {'email': 'bad'}, {'display_name': '   '}):
            with self.subTest(change=list(change)):
                result = self.client.post('/auth/register', json={**good, **change})
                self.assertEqual(result.status_code, 422, result.text)
                self.assertNotIn(self.password, result.text)
                self.assertNotIn('"input"', result.text)
        self.assertEqual(self.value('SELECT count(*) AS value FROM app_user WHERE email=%s', (self.email,)), 0)

    def test_login_me_logout_and_token_hash_only(self):
        user = self.register()
        response = self.client.post('/auth/login', json={'email': self.email.upper(), 'password': self.password})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        token = response.json()['access_token']
        self.assertEqual(response.json()['expires_in'], 3600)
        headers = self.headers(token)
        self.assertEqual(self.client.get('/auth/me', headers=headers).json(), user)
        with self.admin() as conn:
            stored = conn.execute('SELECT token_hash FROM auth_session WHERE user_id=%s', (user['user_id'],)).fetchone()['token_hash']
        self.assertEqual(stored, AuthService.digest(token))
        self.assertNotEqual(stored, token)
        self.assertEqual(self.client.post('/auth/logout', headers=headers).status_code, 204)
        self.assertEqual(self.client.get('/auth/me', headers=headers).status_code, 401)

    def test_missing_invalid_expired_and_disabled_tokens(self):
        user = self.register()
        self.assertEqual(self.client.get('/orders').status_code, 401)
        wrong = self.client.get('/auth/me', headers={'Authorization': 'Bearer invalid-token'})
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(wrong.headers['www-authenticate'], 'Bearer')
        token = self.login()['access_token']
        with self.admin() as conn:
            conn.execute("UPDATE auth_session SET created_at=clock_timestamp()-interval '2 hours',expires_at=clock_timestamp()-interval '1 hour' WHERE token_hash=%s", (AuthService.digest(token),))
        self.assertEqual(self.client.get('/auth/me', headers=self.headers(token)).status_code, 401)
        token = self.login()['access_token']
        with self.admin() as conn:
            conn.execute("UPDATE app_user SET status='INACTIVE' WHERE user_id=%s", (user['user_id'],))
        self.assertEqual(self.client.get('/auth/me', headers=self.headers(token)).status_code, 401)
        self.assertEqual(self.client.post('/auth/login', json={'email': self.email, 'password': self.password}).status_code, 401)

    def test_login_failure_counter_commits_and_lockout_expires(self):
        user = self.register()
        for _ in range(5):
            result = self.client.post('/auth/login', json={'email': self.email, 'password': 'wrong-password'})
            self.assertEqual(result.status_code, 401)
        blocked = self.client.post('/auth/login', json={'email': self.email, 'password': self.password})
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.headers['retry-after'], '60')
        self.assertEqual(self.value('SELECT failed_attempts AS value FROM auth_credential WHERE user_id=%s', (user['user_id'],)), 5)
        with self.admin() as conn:
            conn.execute("UPDATE auth_credential SET locked_until=clock_timestamp()-interval '1 second' WHERE user_id=%s", (user['user_id'],))
        self.login()
        self.assertEqual(self.value('SELECT failed_attempts AS value FROM auth_credential WHERE user_id=%s', (user['user_id'],)), 0)
        unknown = self.client.post('/auth/login', json={'email': 'unknown-'+self.email, 'password': self.password})
        self.assertEqual(unknown.status_code, 401)
        self.assertNotIn(self.password, unknown.text)

    def test_catalog_visibility_pagination_and_no_owner_leak(self):
        activities = self.client.get('/activities').json()
        self.assertIn(self.activity, [a['activity_id'] for a in activities])
        sessions = self.client.get('/sessions', params={'activity_id': self.activity, 'limit': 1}).json()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]['session_id'], self.sessions[0])
        # 先由 CLI 用户锁座；公共库存可见状态，但不能看到谁占有座位。
        self.reserve()
        stock = self.client.get(f'/sessions/{self.sessions[0]}/seats').json()
        self.assertEqual(stock[0]['price'], '100.00')
        self.assertEqual(stock[0]['status'], 'LOCKED')
        self.assertNotIn('locked_order_id', stock[0])
        self.assertNotIn('sold_order_id', stock[0])
        with self.admin() as conn:
            conn.execute("UPDATE activity SET status='DRAFT' WHERE activity_id=%s", (self.activity,))
        self.assertNotIn(self.activity, [a['activity_id'] for a in self.client.get('/activities').json()])
        self.assertEqual(self.client.get('/sessions', params={'activity_id': self.activity}).json(), [])
        self.assertEqual(self.client.get(f'/sessions/{self.sessions[0]}/seats').status_code, 404)
        for path in ('/activities?limit=101','/orders?offset=-1','/sessions?activity_id=-1','/sessions/0/seats'):
            self.assertEqual(self.client.get(path, headers=self.headers_after_register()).status_code, 422)

    def headers_after_register(self):
        # 上面公共查询测试需要的分页订单请求凭据只创建一次。
        if not hasattr(self, '_registered_headers'):
            self.register()
            self._registered_headers = self.headers()
        return self._registered_headers

    def test_http_purchase_failure_payment_refund_and_idempotency(self):
        self.register()
        headers = self.headers()
        request = uuid4()
        order = self.reserve_api(headers=headers, request=request)
        self.assertEqual(self.reserve_api(headers=headers, request=request)['order_id'], order['order_id'])
        path = f"/orders/{order['order_id']}"
        failed = self.client.post(path+'/payments', headers=headers, json={'amount':'220.00','request_id':str(uuid4()),'outcome':'FAILED'})
        self.assertEqual(failed.status_code, 200, failed.text)
        self.assertEqual(failed.json()['order']['status'], 'PENDING')
        body = {'amount':'220.00','request_id':str(uuid4())}
        paid = self.client.post(path+'/payments', headers=headers, json=body)
        self.assertEqual(paid.status_code, 200, paid.text)
        self.assertEqual(paid.json()['order']['status'], 'PAID')
        self.assertEqual(len(paid.json()['order']['tickets']), 2)
        self.assertEqual(paid.json()['payment']['amount'], '220.00')
        self.assertEqual(self.client.post(path+'/payments',headers=headers,json=body).json(), paid.json())
        body = {'reason':'HTTP 整单退款','request_id':str(uuid4())}
        refunded = self.client.post(path+'/refunds',headers=headers,json=body)
        self.assertEqual(refunded.status_code, 200, refunded.text)
        self.assertEqual(refunded.json()['order']['status'], 'REFUNDED')
        self.assertEqual(self.client.post(path+'/refunds',headers=headers,json=body).json(), refunded.json())
        detail = self.client.get(path,headers=headers).json()
        self.assertTrue(all(t['status']=='REFUNDED' for t in detail['tickets']))
        self.assertEqual([h['to_status'] for h in detail['history']], ['PENDING','PAID','REFUNDED'])
        self.assertEqual(self.client.get('/orders',headers=headers).json()[0]['order_id'], order['order_id'])

    def test_unauthorized_user_cannot_read_cancel_pay_or_refund(self):
        self.register()
        own_headers = self.headers()
        order = self.reserve_api(headers=own_headers)
        other_email = 'other-'+self.email
        self.register(email=other_email)
        other = self.headers(self.login(email=other_email)['access_token'])
        path = f"/orders/{order['order_id']}"
        self.assertEqual(self.client.get(path,headers=other).status_code, 404)
        self.assertEqual(self.client.post(path+'/cancel',headers=other).status_code, 404)
        self.assertEqual(self.client.post(path+'/payments',headers=other,json={'amount':'220','request_id':str(uuid4())}).status_code, 404)
        self.assertEqual(self.client.post(path+'/refunds',headers=other,json={'reason':'非本人','request_id':str(uuid4())}).status_code, 404)
        self.assertEqual(self.client.get('/orders',headers=other).json(), [])
        self.assertEqual(self.client.get(path,headers=own_headers).json()['status'],'PENDING')
        absent=self.client.get('/orders/9223372036854775807',headers=other)
        self.assertEqual(absent.status_code,404)
        self.assertEqual(absent.json(),self.client.get(path,headers=other).json())

    def test_body_validation_and_request_conflict_have_safe_errors(self):
        self.register()
        headers = self.headers()
        good={'session_id':self.sessions[0],'inventory_ids':self.inventory[0],'request_id':str(uuid4())}
        for change in ({'user_id':self.users[1]},{'session_id':True},{'inventory_ids':[True]},
                       {'inventory_ids':[self.inventory[0][0]]*2},{'inventory_ids':[]},{'request_id':'bad'},
                       {'hold_seconds':1},{'session_id':9223372036854775808}):
            result=self.client.post('/orders',headers=headers,json={**good,**change})
            self.assertEqual(result.status_code,422,result.text)
            self.assertEqual(result.json()['error']['code'],'invalid_request')
        order=self.reserve_api(headers=headers,request=good['request_id'])
        conflict=self.client.post('/orders',headers=headers,json={**good,'inventory_ids':[self.inventory[0][0]]})
        self.assertEqual(conflict.status_code,409)
        self.assertEqual(conflict.json()['error']['code'],'request_conflict')
        path=f"/orders/{order['order_id']}/payments"
        for amount in (220.0,'NaN','1.001','-1'):
            self.assertEqual(self.client.post(path,headers=headers,json={'amount':amount,'request_id':str(uuid4())}).status_code,422)
        mismatch=self.client.post(path,headers=headers,json={'amount':'1.00','request_id':str(uuid4())})
        self.assertEqual(mismatch.status_code,409)

    def test_cancel_and_expired_payment_http_results(self):
        user=self.register()
        headers=self.headers()
        order=self.reserve_api(headers=headers)
        path=f"/orders/{order['order_id']}"
        self.assertEqual(self.client.post(path+'/cancel',headers=headers).json()['status'],'CANCELLED')
        self.assertEqual(self.client.post(path+'/cancel',headers=headers).json()['status'],'CANCELLED')
        # CLI 可指定短锁定期；HTTP 端不开放任意调整占座时长。
        short=self.service(seconds=1).reserve(user['user_id'],self.sessions[0],self.inventory[0])
        self.wait_expired(short)
        result=self.client.post(f"/orders/{short['order_id']}/payments",headers=headers,
                                json={'amount':'220.00','request_id':str(uuid4())})
        self.assertEqual(result.status_code,200,result.text)
        self.assertIsNone(result.json()['payment'])
        self.assertEqual(result.json()['order']['status'],'EXPIRED')

    def test_health_and_openapi_documents_auth_and_decimal_strings(self):
        self.assertEqual(self.client.get('/health/live').json(),{'status':'ok'})
        self.assertEqual(self.client.get('/health/ready').json(),{'status':'ready'})
        self.assertEqual(self.client.get('/docs').status_code,200)
        spec=self.client.get('/openapi.json').json()
        self.assertIn('HTTPBearer',spec['components']['securitySchemes'])
        self.assertIn('security',spec['paths']['/orders']['post'])
        self.assertIn('/orders/{order_id}/refunds',spec['paths'])
        self.assertEqual(spec['components']['schemas']['PaymentInput']['properties']['amount']['type'],'string')
        self.assertEqual(spec['paths']['/orders']['post']['responses']['422']['content']['application/json']['schema']['$ref'],
                         '#/components/schemas/ErrorResponse')
        self.assertEqual(self.client.post('/does-not-exist').status_code,404)

    def test_database_failures_are_503_without_connection_details(self):
        def broken():
            raise psycopg.OperationalError('sensitive connection configuration')
        app=create_app(broken)
        with TestClient(app) as client:
            self.assertEqual(client.get('/health/live').status_code,200)
            result=client.get('/health/ready')
        self.assertEqual(result.status_code,503)
        self.assertNotIn('sensitive',result.text)

    def test_registration_is_atomic_if_credential_insert_fails(self):
        # 注入凭据表写入错误，保证用户资料不会单独提交。
        with self.admin() as conn:
            conn.execute("""CREATE FUNCTION reject_test_credential() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN RAISE EXCEPTION 'test credential write failure'; END $$""")
            conn.execute('CREATE TRIGGER reject_test_credential BEFORE INSERT ON auth_credential FOR EACH ROW EXECUTE FUNCTION reject_test_credential()')
        try:
            result=self.client.post('/auth/register',json={'email':self.email,'password':self.password,'display_name':'回滚验证'})
            self.assertEqual(result.status_code,503)
            self.assertEqual(self.value('SELECT count(*) AS value FROM app_user WHERE email=%s',(self.email,)),0)
        finally:
            with self.admin() as conn:
                conn.execute('DROP TRIGGER reject_test_credential ON auth_credential')
                conn.execute('DROP FUNCTION reject_test_credential()')

    def test_two_http_connections_reserve_one_seat(self):
        self.register()
        a=self.headers()
        email='race-'+self.email
        self.register(email=email)
        b=self.headers(self.login(email=email)['access_token'])
        gate=threading.Barrier(2)
        def reserve(headers):
            gate.wait(timeout=5)
            return self.client.post('/orders',headers=headers,json={'session_id':self.sessions[0],
                'inventory_ids':[self.inventory[0][0]],'request_id':str(uuid4())})
        with ThreadPoolExecutor(max_workers=2) as pool:
            first,second=pool.submit(reserve,a),pool.submit(reserve,b)
            results=[first.result(timeout=15),second.result(timeout=15)]
        self.assertEqual(sorted(r.status_code for r in results),[201,409])
        self.assertEqual(self.value('SELECT count(*) AS value FROM booking_order WHERE session_id=%s',(self.sessions[0],)),1)

    def test_migration_rerun_and_auth_database_permissions(self):
        with self.admin() as conn:
            migrate(conn)
            grant_app(conn)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with self.runtime() as conn:
                conn.execute('DELETE FROM auth_credential')
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with self.runtime() as conn:
                conn.execute('UPDATE app_user SET role=\'ADMIN\'')

    def test_web_entry_assets_and_api_paths_are_independent(self):
        home=self.client.get('/')
        self.assertEqual(home.status_code,200)
        self.assertIn('text/html',home.headers['content-type'])
        self.assertIn('活动与票务',home.text)
        self.assertIn("script-src 'self'",home.headers['content-security-policy'])
        self.assertEqual(home.headers['x-content-type-options'],'nosniff')
        for path in ('/assets/styles.css','/assets/app.js','/assets/lib.mjs','/assets/i18n.mjs'):
            self.assertEqual(self.client.get(path).status_code,200,path)
        self.assertEqual(self.client.get('/assets/mark.svg').status_code,404)
        self.assertEqual(self.client.get('/assets/%2e%2e/auth.py').status_code,404)
        self.assertEqual(self.client.get('/docs').status_code,200)
        self.assertEqual(self.client.get('/health/ready').status_code,200)
        self.assertEqual(self.client.get('/orders').status_code,401)

    def test_order_display_metadata_remains_visible_when_catalog_closes(self):
        self.register()
        headers=self.headers()
        order=self.reserve_api(headers=headers)
        self.assertEqual(order['activity_title'],'事务测试活动')
        self.assertEqual(order['venue_name'],'事务测试场馆')
        self.assertEqual([item['seat_code'] for item in order['items']],['A1','A2'])
        self.assertIsNotNone(order['session_start_time'])
        with self.admin() as conn:
            conn.execute("UPDATE activity SET status='ARCHIVED' WHERE activity_id=%s",(self.activity,))
        self.assertEqual(self.client.get('/sessions',params={'activity_id':self.activity}).json(),[])
        detail=self.client.get(f"/orders/{order['order_id']}",headers=headers).json()
        self.assertEqual(detail['activity_title'],order['activity_title'])
        summary=self.client.get('/orders',headers=headers).json()[0]
        self.assertEqual(summary['activity_title'],order['activity_title'])
        self.assertEqual(summary['session_start_time'],order['session_start_time'])
