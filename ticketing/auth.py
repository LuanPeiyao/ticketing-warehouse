"""密码登录与数据库会话。令牌明文只在登录成功时交给调用者。"""
from datetime import timedelta
import hashlib
import re
import secrets

import psycopg
from pwdlib import PasswordHash

from .db import connect


class AuthError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code = status, code


class AuthService:
    def __init__(self, connect_factory=connect):
        self.connect = connect_factory
        self.passwords = PasswordHash.recommended()
        # 未知邮箱也执行一次 Argon2 校验，避免直接跳过密码计算。
        self.dummy_hash = self.passwords.hash(secrets.token_urlsafe(32))

    @staticmethod
    def digest(token):
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def public_user(row):
        return {k: row[k] for k in ('user_id', 'email', 'display_name', 'role', 'status')}

    def register(self, email, display_name, password):
        email = email.strip().lower()
        password_hash = self.passwords.hash(password)
        try:
            with self.connect() as conn, conn.transaction():
                user = conn.execute("""INSERT INTO app_user (email,display_name,role)
                    VALUES (%s,%s,'CUSTOMER') RETURNING *""", (email, display_name,)).fetchone()
                conn.execute('INSERT INTO auth_credential (user_id,password_hash) VALUES (%s,%s)',
                             (user['user_id'], password_hash))
                return self.public_user(user)
        except psycopg.errors.UniqueViolation as exc:
            raise AuthError(409, 'email_registered', '该邮箱已被使用，不能通过注册接管已有账号') from exc

    def login(self, email, password):
        failure = None
        with self.connect() as conn, conn.transaction():
            user = conn.execute('''SELECT u.*,c.password_hash,c.failed_attempts,c.locked_until
                FROM app_user u JOIN auth_credential c USING (user_id)
                WHERE lower(btrim(u.email))=%s FOR UPDATE OF c''', (email.strip().lower(),)).fetchone()
            now = conn.execute('SELECT clock_timestamp() AS value').fetchone()['value']
            if user and user['locked_until'] and user['locked_until'] > now:
                failure = AuthError(429, 'login_throttled', '登录尝试过多，请一分钟后重试')
            else:
                valid = self.passwords.verify(password, user['password_hash'] if user else self.dummy_hash)
                if not user or not valid or user['status'] != 'ACTIVE':
                    if user and user['status'] == 'ACTIVE':
                        count = 0 if user['locked_until'] and user['locked_until'] <= now else user['failed_attempts']
                        count = min(count + 1, 5)
                        conn.execute('UPDATE auth_credential SET failed_attempts=%s,locked_until=%s WHERE user_id=%s',
                                     (count, now + timedelta(minutes=1) if count == 5 else None, user['user_id']))
                    failure = AuthError(401, 'invalid_credentials', '邮箱或密码错误，或账号不可用')
                else:
                    conn.execute('UPDATE auth_credential SET failed_attempts=0,locked_until=NULL WHERE user_id=%s', (user['user_id'],))
                    token = secrets.token_urlsafe(32)
                    # 哈希计算后重新取时间，避免将等待和密码校验时间计入令牌有效期。
                    expires = conn.execute('''INSERT INTO auth_session (token_hash,user_id,expires_at)
                        VALUES (%s,%s,clock_timestamp()+interval '1 hour') RETURNING expires_at''',
                        (self.digest(token), user['user_id'])).fetchone()['expires_at']
                    return {'access_token': token, 'token_type': 'bearer', 'expires_in': 3600, 'expires_at': expires}
        # 错误在事务提交之后抛出，确保失败次数确实持久化。
        raise failure

    def authenticate(self, token):
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
            raise AuthError(401, 'invalid_token', '登录已失效，请重新登录')
        with self.connect() as conn:
            user = conn.execute('''SELECT u.* FROM auth_session s JOIN app_user u USING (user_id)
                WHERE s.token_hash=%s AND s.revoked_at IS NULL AND s.expires_at>clock_timestamp()
                AND u.status='ACTIVE' ''', (self.digest(token),)).fetchone()
        if not user:
            raise AuthError(401, 'invalid_token', '登录已失效，请重新登录')
        return self.public_user(user)

    def logout(self, token):
        with self.connect() as conn:
            conn.execute('UPDATE auth_session SET revoked_at=clock_timestamp() WHERE token_hash=%s AND revoked_at IS NULL',
                         (self.digest(token),))
