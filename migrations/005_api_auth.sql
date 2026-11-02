-- API 登录凭据与可撤销会话；现有用户不自动分配密码。
BEGIN;
SET LOCAL search_path = public;
SET LOCAL lock_timeout = '5s';

CREATE TABLE auth_credential (
    user_id BIGINT PRIMARY KEY REFERENCES app_user (user_id) ON DELETE RESTRICT,
    password_hash TEXT NOT NULL CHECK (password_hash LIKE '$argon2id$%'),
    failed_attempts INTEGER NOT NULL DEFAULT 0 CHECK (failed_attempts BETWEEN 0 AND 5),
    locked_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE auth_session (
    token_hash CHAR(64) PRIMARY KEY CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    user_id BIGINT NOT NULL REFERENCES auth_credential (user_id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    CONSTRAINT ck_auth_session_expiry CHECK (expires_at>created_at),
    CONSTRAINT ck_auth_session_revocation CHECK (revoked_at IS NULL OR revoked_at>=created_at)
);
CREATE INDEX ix_auth_session_user ON auth_session (user_id,expires_at);
COMMENT ON TABLE auth_credential IS 'Argon2id 密码哈希；连续五次登录失败锁定一分钟，无默认密码';
COMMENT ON TABLE auth_session IS '随机 Bearer 令牌的 SHA256 摘要；一小时过期，退出时撤销，无明文令牌';
COMMIT;
