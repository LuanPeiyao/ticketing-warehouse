-- ODS 贴源层：业务库 public schema 的原样快照。
-- 设计要点：
--   1. 每次抽取生成一个批次（ods.extract_batch），所有快照行携带 _batch_id；
--   2. ODS 只追加、不修改，保留每一批的原始形态，可回溯历史；
--   3. 列名、类型与 public 完全一致，不在此层做任何清洗或加工；
--   4. 认证相关表（auth_credential / auth_session）含敏感信息且与交易分析无关，不抽取。
BEGIN;
SET LOCAL lock_timeout = '10s';

CREATE SCHEMA IF NOT EXISTS ods;

-- 批次登记表：每次抽取一行，DWD 装载只消费 status='DONE' 的最新批次。
CREATE TABLE IF NOT EXISTS ods.extract_batch (
    batch_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    finished_at TIMESTAMPTZ,
    source_rows BIGINT NOT NULL DEFAULT 0,
    status      VARCHAR(16) NOT NULL DEFAULT 'RUNNING'
                CHECK (status IN ('RUNNING', 'DONE', 'FAILED')),
    note        TEXT NOT NULL DEFAULT ''
);

-- 用户
CREATE TABLE IF NOT EXISTS ods.app_user (
    user_id      BIGINT NOT NULL,
    email        VARCHAR(254) NOT NULL,
    display_name VARCHAR(100) NOT NULL,
    role         VARCHAR(20) NOT NULL,
    status       VARCHAR(20) NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL,
    _batch_id    BIGINT NOT NULL,
    _extracted_at TIMESTAMPTZ NOT NULL
);

-- 场馆
CREATE TABLE IF NOT EXISTS ods.venue (
    venue_id     BIGINT NOT NULL,
    name         VARCHAR(200) NOT NULL,
    address      TEXT NOT NULL,
    status       VARCHAR(20) NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL,
    _batch_id    BIGINT NOT NULL,
    _extracted_at TIMESTAMPTZ NOT NULL
);

-- 物理座位
CREATE TABLE IF NOT EXISTS ods.seat (
    seat_id      BIGINT NOT NULL,
    venue_id     BIGINT NOT NULL,
    seat_code    VARCHAR(32) NOT NULL,
    row_label    VARCHAR(16) NOT NULL,
    seat_number  INTEGER NOT NULL,
    status       VARCHAR(20) NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL,
    _batch_id    BIGINT NOT NULL,
    _extracted_at TIMESTAMPTZ NOT NULL
);

-- 活动
CREATE TABLE IF NOT EXISTS ods.activity (
    activity_id  BIGINT NOT NULL,
    title        VARCHAR(200) NOT NULL,
    description  TEXT NOT NULL,
    organizer_id BIGINT NOT NULL,
    status       VARCHAR(20) NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL,
    _batch_id    BIGINT NOT NULL,
    _extracted_at TIMESTAMPTZ NOT NULL
);

-- 场次
CREATE TABLE IF NOT EXISTS ods.activity_session (
    session_id   BIGINT NOT NULL,
    activity_id  BIGINT NOT NULL,
    venue_id     BIGINT NOT NULL,
    start_time   TIMESTAMPTZ NOT NULL,
    end_time     TIMESTAMPTZ NOT NULL,
    status       VARCHAR(20) NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL,
    _batch_id    BIGINT NOT NULL,
    _extracted_at TIMESTAMPTZ NOT NULL
);

-- 场次库存（可售单元）
CREATE TABLE IF NOT EXISTS ods.session_seat (
    session_seat_id BIGINT NOT NULL,
    session_id      BIGINT NOT NULL,
    seat_id         BIGINT NOT NULL,
    venue_id        BIGINT NOT NULL,
    price           NUMERIC(12, 2) NOT NULL,
    status          VARCHAR(20) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL,
    _batch_id       BIGINT NOT NULL,
    _extracted_at   TIMESTAMPTZ NOT NULL
);

-- 订单
CREATE TABLE IF NOT EXISTS ods.booking_order (
    order_id     BIGINT NOT NULL,
    order_no     VARCHAR(40) NOT NULL,
    user_id      BIGINT NOT NULL,
    session_id   BIGINT NOT NULL,
    request_id   UUID NOT NULL,
    total_amount NUMERIC(14, 2) NOT NULL,
    status       VARCHAR(20) NOT NULL,
    expires_at   TIMESTAMPTZ NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL,
    _batch_id    BIGINT NOT NULL,
    _extracted_at TIMESTAMPTZ NOT NULL
);

-- 订单明细（座位粒度）
CREATE TABLE IF NOT EXISTS ods.booking_item (
    item_id         BIGINT NOT NULL,
    order_id        BIGINT NOT NULL,
    session_id      BIGINT NOT NULL,
    session_seat_id BIGINT NOT NULL,
    unit_price      NUMERIC(12, 2) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL,
    _batch_id       BIGINT NOT NULL,
    _extracted_at   TIMESTAMPTZ NOT NULL
);

-- 订单状态流转历史
CREATE TABLE IF NOT EXISTS ods.order_status_history (
    history_id    BIGINT NOT NULL,
    order_id      BIGINT NOT NULL,
    from_status   VARCHAR(20),
    to_status     VARCHAR(20) NOT NULL,
    reason        VARCHAR(100) NOT NULL,
    actor_user_id BIGINT,
    changed_at    TIMESTAMPTZ NOT NULL,
    _batch_id     BIGINT NOT NULL,
    _extracted_at TIMESTAMPTZ NOT NULL
);

-- 支付尝试
CREATE TABLE IF NOT EXISTS ods.payment_attempt (
    payment_id    BIGINT NOT NULL,
    payment_no    VARCHAR(40) NOT NULL,
    order_id      BIGINT NOT NULL,
    request_id    UUID NOT NULL,
    amount        NUMERIC(14, 2) NOT NULL,
    provider      VARCHAR(20) NOT NULL,
    status        VARCHAR(20) NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL,
    _batch_id     BIGINT NOT NULL,
    _extracted_at TIMESTAMPTZ NOT NULL
);

-- 电子票
CREATE TABLE IF NOT EXISTS ods.ticket (
    ticket_id     BIGINT NOT NULL,
    ticket_no     UUID NOT NULL,
    item_id       BIGINT NOT NULL,
    order_id      BIGINT NOT NULL,
    session_seat_id BIGINT NOT NULL,
    status        VARCHAR(20) NOT NULL,
    issued_at     TIMESTAMPTZ NOT NULL,
    refunded_at   TIMESTAMPTZ,
    _batch_id     BIGINT NOT NULL,
    _extracted_at TIMESTAMPTZ NOT NULL
);

-- 退款尝试
CREATE TABLE IF NOT EXISTS ods.refund_attempt (
    refund_id     BIGINT NOT NULL,
    refund_no     VARCHAR(40) NOT NULL,
    payment_id    BIGINT NOT NULL,
    order_id      BIGINT NOT NULL,
    request_id    UUID NOT NULL,
    amount        NUMERIC(14, 2) NOT NULL,
    reason        VARCHAR(200) NOT NULL,
    status        VARCHAR(20) NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL,
    _batch_id     BIGINT NOT NULL,
    _extracted_at TIMESTAMPTZ NOT NULL
);

-- 批次内查询与后续 join 的支撑索引（按批次裁剪是 ODS 最常用的访问路径）。
CREATE INDEX IF NOT EXISTS ix_ods_order_batch ON ods.booking_order (_batch_id);
CREATE INDEX IF NOT EXISTS ix_ods_item_batch ON ods.booking_item (_batch_id);
CREATE INDEX IF NOT EXISTS ix_ods_history_batch ON ods.order_status_history (_batch_id);
CREATE INDEX IF NOT EXISTS ix_ods_payment_batch ON ods.payment_attempt (_batch_id);
CREATE INDEX IF NOT EXISTS ix_ods_ticket_batch ON ods.ticket (_batch_id);
CREATE INDEX IF NOT EXISTS ix_ods_refund_batch ON ods.refund_attempt (_batch_id);
CREATE INDEX IF NOT EXISTS ix_ods_session_seat_batch ON ods.session_seat (_batch_id);

COMMIT;
