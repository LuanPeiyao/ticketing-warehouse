-- 订单与锁座阶段。保留 001/002 和已有数据，通过新迁移扩展。
BEGIN;
SET LOCAL search_path = public;
SET LOCAL lock_timeout = '5s';

CREATE TABLE booking_order (
    order_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_no VARCHAR(40) NOT NULL UNIQUE,
    user_id BIGINT NOT NULL REFERENCES app_user (user_id) ON DELETE RESTRICT,
    session_id BIGINT NOT NULL REFERENCES activity_session (session_id) ON DELETE RESTRICT,
    request_id UUID NOT NULL,
    total_amount NUMERIC(14, 2) NOT NULL DEFAULT 0,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    CONSTRAINT uq_order_user_request UNIQUE (user_id, request_id),
    CONSTRAINT uq_order_id_session UNIQUE (order_id, session_id),
    CONSTRAINT ck_order_amount CHECK (total_amount >= 0 AND total_amount < 1000000000000),
    CONSTRAINT ck_order_status CHECK (status IN ('PENDING', 'CANCELLED', 'EXPIRED')),
    CONSTRAINT ck_order_expiry CHECK (expires_at > created_at)
);
CREATE INDEX ix_order_user_created ON booking_order (user_id, created_at DESC);
CREATE INDEX ix_order_pending_expiry ON booking_order (expires_at, order_id) WHERE status = 'PENDING';
CREATE TRIGGER trg_booking_order_updated_at
BEFORE UPDATE ON booking_order FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

ALTER TABLE session_seat ADD CONSTRAINT uq_inventory_id_session UNIQUE (session_seat_id, session_id);
CREATE TABLE booking_item (
    item_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id BIGINT NOT NULL,
    session_id BIGINT NOT NULL,
    session_seat_id BIGINT NOT NULL,
    unit_price NUMERIC(12, 2) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    CONSTRAINT uq_order_item_inventory UNIQUE (order_id, session_seat_id),
    CONSTRAINT fk_item_order_session FOREIGN KEY (order_id, session_id)
        REFERENCES booking_order (order_id, session_id) ON DELETE RESTRICT,
    CONSTRAINT fk_item_inventory_session FOREIGN KEY (session_seat_id, session_id)
        REFERENCES session_seat (session_seat_id, session_id) ON DELETE RESTRICT,
    CONSTRAINT ck_item_price CHECK (unit_price >= 0 AND unit_price < 10000000000)
);
CREATE INDEX ix_item_inventory ON booking_item (session_seat_id);

CREATE TABLE order_status_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES booking_order (order_id) ON DELETE RESTRICT,
    from_status VARCHAR(20),
    to_status VARCHAR(20) NOT NULL,
    reason VARCHAR(100) NOT NULL,
    actor_user_id BIGINT REFERENCES app_user (user_id) ON DELETE RESTRICT,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT uq_order_history_state UNIQUE (order_id, to_status),
    CONSTRAINT ck_order_history_transition CHECK (
        (from_status IS NULL AND to_status = 'PENDING') OR
        (from_status IS NOT NULL AND from_status = 'PENDING' AND to_status IN ('CANCELLED', 'EXPIRED'))
    )
);

ALTER TABLE session_seat
    ADD COLUMN locked_order_id BIGINT,
    ADD COLUMN locked_until TIMESTAMPTZ,
    DROP CONSTRAINT ck_session_seat_status;
ALTER TABLE session_seat
    ADD CONSTRAINT fk_inventory_lock_order FOREIGN KEY (locked_order_id, session_id)
        REFERENCES booking_order (order_id, session_id) ON DELETE RESTRICT,
    ADD CONSTRAINT ck_session_seat_status CHECK (status IN ('AVAILABLE', 'LOCKED')),
    ADD CONSTRAINT ck_inventory_lock_shape CHECK (
        (status = 'AVAILABLE' AND locked_order_id IS NULL AND locked_until IS NULL) OR
        (status = 'LOCKED' AND locked_order_id IS NOT NULL AND locked_until IS NOT NULL)
    );
CREATE INDEX ix_inventory_locked_order ON session_seat (locked_order_id, session_seat_id)
    WHERE locked_order_id IS NOT NULL;

COMMENT ON TABLE booking_order IS '订单；当前支持待支付、取消、过期；request_id 用于用户级幂等重试';
COMMENT ON TABLE booking_item IS '一行代表订单中的一个场次座位；unit_price 保存下单价格快照';
COMMENT ON TABLE order_status_history IS '订单创建及终态历史；本阶段禁止同一订单重复写入同一状态';
COMMENT ON TABLE session_seat IS '场次库存；AVAILABLE 可预订，LOCKED 关联待支付订单和截止时间';
COMMIT;
