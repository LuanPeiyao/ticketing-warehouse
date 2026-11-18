-- DWD 明细层：维度表 + 事实表。
-- 建模说明：
--   1. 维度表做轻度加工：拼接名称、派生时间属性（东八区本地日期/小时/星期）；
--   2. 事实表以「一单一行」为粒度组织订单事实，把支付尝试、退款尝试、
--      状态历史的明细聚合为订单上的度量列，直接服务 DWS 汇总；
--   3. 支付 / 退款 / 票另保留过程事实表（保留每次尝试的原始粒度），
--      供失败率、退款时长等过程类指标使用；
--   4. 所有时间属性统一在维度层完成 UTC -> Asia/Shanghai 的口径转换，
--      上层（DWS/ADS）不再自行做时区换算。
BEGIN;
SET LOCAL lock_timeout = '10s';

CREATE SCHEMA IF NOT EXISTS dwd;

-- ============ 维度表 ============

CREATE TABLE IF NOT EXISTS dwd.dim_user (
    user_id      BIGINT PRIMARY KEY,
    email        VARCHAR(254) NOT NULL,
    email_domain VARCHAR(100) NOT NULL,
    display_name VARCHAR(100) NOT NULL,
    role         VARCHAR(20) NOT NULL,
    status       VARCHAR(20) NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS dwd.dim_venue (
    venue_id   BIGINT PRIMARY KEY,
    name       VARCHAR(200) NOT NULL,
    address    TEXT NOT NULL,
    status     VARCHAR(20) NOT NULL,
    seat_count INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS dwd.dim_activity (
    activity_id     BIGINT PRIMARY KEY,
    title           VARCHAR(200) NOT NULL,
    organizer_id    BIGINT NOT NULL,
    organizer_name  VARCHAR(100) NOT NULL,
    activity_status VARCHAR(20) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS dwd.dim_session (
    session_id       BIGINT PRIMARY KEY,
    activity_id      BIGINT NOT NULL,
    activity_title   VARCHAR(200) NOT NULL,
    venue_id         BIGINT NOT NULL,
    venue_name       VARCHAR(200) NOT NULL,
    start_time       TIMESTAMPTZ NOT NULL,
    end_time         TIMESTAMPTZ NOT NULL,
    duration_minutes INTEGER NOT NULL,
    session_status   VARCHAR(20) NOT NULL,
    local_date       DATE NOT NULL,
    local_start_hour SMALLINT NOT NULL,
    day_of_week      SMALLINT NOT NULL,
    is_weekend       BOOLEAN NOT NULL
);

-- ============ 事实表 ============

-- 订单事实：一单一行；支付/退款/历史明细聚合为度量列。
CREATE TABLE IF NOT EXISTS dwd.fact_order (
    order_id             BIGINT PRIMARY KEY,
    order_no             VARCHAR(40) NOT NULL,
    user_id              BIGINT NOT NULL,
    session_id           BIGINT NOT NULL,
    request_id           UUID NOT NULL,
    total_amount         NUMERIC(14, 2) NOT NULL,
    order_status         VARCHAR(20) NOT NULL,
    order_created_at     TIMESTAMPTZ NOT NULL,
    expires_at           TIMESTAMPTZ NOT NULL,
    item_count           INTEGER NOT NULL,
    payment_attempt_count INTEGER NOT NULL,
    payment_failed_count INTEGER NOT NULL,
    first_payment_at     TIMESTAMPTZ,
    paid_at              TIMESTAMPTZ,
    hold_minutes         NUMERIC(12, 2),
    refund_count         INTEGER NOT NULL,
    refunded_at          TIMESTAMPTZ,
    refund_delay_minutes NUMERIC(12, 2),
    final_reason         VARCHAR(100)
);

-- 支付过程事实：保留每次尝试（含失败），供支付成功率指标使用。
CREATE TABLE IF NOT EXISTS dwd.fact_payment (
    payment_id    BIGINT PRIMARY KEY,
    payment_no    VARCHAR(40) NOT NULL,
    order_id      BIGINT NOT NULL,
    amount        NUMERIC(14, 2) NOT NULL,
    payment_status VARCHAR(20) NOT NULL,
    provider      VARCHAR(20) NOT NULL,
    attempted_at  TIMESTAMPTZ NOT NULL
);

-- 退款过程事实：保留每次尝试。
CREATE TABLE IF NOT EXISTS dwd.fact_refund (
    refund_id     BIGINT PRIMARY KEY,
    refund_no     VARCHAR(40) NOT NULL,
    order_id      BIGINT NOT NULL,
    payment_id    BIGINT NOT NULL,
    amount        NUMERIC(14, 2) NOT NULL,
    refund_status VARCHAR(20) NOT NULL,
    reason        VARCHAR(200) NOT NULL,
    attempted_at  TIMESTAMPTZ NOT NULL
);

-- 票事实：一张明细一张票。
CREATE TABLE IF NOT EXISTS dwd.fact_ticket (
    ticket_id       BIGINT PRIMARY KEY,
    ticket_no       UUID NOT NULL,
    order_id        BIGINT NOT NULL,
    session_id      BIGINT NOT NULL,
    session_seat_id BIGINT NOT NULL,
    seat_code       VARCHAR(32) NOT NULL,
    row_label       VARCHAR(16) NOT NULL,
    ticket_status   VARCHAR(20) NOT NULL,
    issued_at       TIMESTAMPTZ NOT NULL,
    refunded_at     TIMESTAMPTZ
);

-- ============ 查询支撑索引 ============

CREATE INDEX IF NOT EXISTS ix_dwd_fact_order_user    ON dwd.fact_order (user_id);
CREATE INDEX IF NOT EXISTS ix_dwd_fact_order_session ON dwd.fact_order (session_id);
CREATE INDEX IF NOT EXISTS ix_dwd_fact_order_status  ON dwd.fact_order (order_status);
CREATE INDEX IF NOT EXISTS ix_dwd_fact_order_created ON dwd.fact_order (order_created_at);
CREATE INDEX IF NOT EXISTS ix_dwd_fact_ticket_order  ON dwd.fact_ticket (order_id);
CREATE INDEX IF NOT EXISTS ix_dwd_fact_ticket_session ON dwd.fact_ticket (session_id);
CREATE INDEX IF NOT EXISTS ix_dwd_fact_payment_order ON dwd.fact_payment (order_id);
CREATE INDEX IF NOT EXISTS ix_dwd_fact_refund_order  ON dwd.fact_refund (order_id);
CREATE INDEX IF NOT EXISTS ix_dwd_dim_session_date   ON dwd.dim_session (local_date);

COMMIT;
