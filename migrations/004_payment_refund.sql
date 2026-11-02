-- 同步模拟支付与整单退款；不接入资金通道，不包含检票。
BEGIN;
SET LOCAL search_path = public;
SET LOCAL lock_timeout = '5s';

ALTER TABLE booking_order
    ADD COLUMN paid_at TIMESTAMPTZ,
    ADD COLUMN refunded_at TIMESTAMPTZ,
    DROP CONSTRAINT ck_order_status,
    ADD CONSTRAINT ck_order_status CHECK (status IN ('PENDING','CANCELLED','EXPIRED','PAID','REFUNDED')),
    ADD CONSTRAINT ck_order_payment_shape CHECK (
        (status IN ('PENDING','CANCELLED','EXPIRED') AND paid_at IS NULL AND refunded_at IS NULL) OR
        (status='PAID' AND paid_at IS NOT NULL AND refunded_at IS NULL) OR
        (status='REFUNDED' AND paid_at IS NOT NULL AND refunded_at IS NOT NULL AND refunded_at>=paid_at)
    );
ALTER TABLE order_status_history DROP CONSTRAINT ck_order_history_transition;
ALTER TABLE order_status_history ADD CONSTRAINT ck_order_history_transition CHECK (
    (from_status IS NULL AND to_status='PENDING') OR
    (from_status IS NOT NULL AND from_status='PENDING' AND to_status IN ('CANCELLED','EXPIRED','PAID')) OR
    (from_status IS NOT NULL AND from_status='PAID' AND to_status='REFUNDED')
);

ALTER TABLE session_seat
    ADD COLUMN sold_order_id BIGINT,
    DROP CONSTRAINT ck_session_seat_status,
    DROP CONSTRAINT ck_inventory_lock_shape;
ALTER TABLE session_seat
    ADD CONSTRAINT fk_inventory_sold_order FOREIGN KEY (sold_order_id,session_id)
        REFERENCES booking_order (order_id,session_id) ON DELETE RESTRICT,
    ADD CONSTRAINT ck_session_seat_status CHECK (status IN ('AVAILABLE','LOCKED','SOLD')),
    ADD CONSTRAINT ck_inventory_lock_shape CHECK (
        (status='AVAILABLE' AND locked_order_id IS NULL AND locked_until IS NULL AND sold_order_id IS NULL) OR
        (status='LOCKED' AND locked_order_id IS NOT NULL AND locked_until IS NOT NULL AND sold_order_id IS NULL) OR
        (status='SOLD' AND locked_order_id IS NULL AND locked_until IS NULL AND sold_order_id IS NOT NULL)
    );
CREATE INDEX ix_inventory_sold_order ON session_seat (sold_order_id,session_seat_id)
    WHERE sold_order_id IS NOT NULL;

CREATE TABLE payment_attempt (
    payment_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    payment_no VARCHAR(40) NOT NULL UNIQUE,
    order_id BIGINT NOT NULL REFERENCES booking_order (order_id) ON DELETE RESTRICT,
    request_id UUID NOT NULL,
    amount NUMERIC(14,2) NOT NULL,
    provider VARCHAR(20) NOT NULL DEFAULT 'SIMULATED' CHECK (provider='SIMULATED'),
    status VARCHAR(20) NOT NULL CHECK (status IN ('SUCCEEDED','FAILED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT uq_payment_request UNIQUE (order_id,request_id),
    CONSTRAINT uq_payment_refund_target UNIQUE (payment_id,order_id,amount),
    CONSTRAINT ck_payment_amount CHECK (amount>=0 AND amount<1000000000000)
);
CREATE UNIQUE INDEX uq_payment_success_order ON payment_attempt (order_id) WHERE status='SUCCEEDED';

ALTER TABLE booking_item ADD CONSTRAINT uq_item_ticket_target UNIQUE (item_id,order_id,session_seat_id);
CREATE TABLE ticket (
    ticket_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticket_no UUID NOT NULL UNIQUE,
    item_id BIGINT NOT NULL UNIQUE,
    order_id BIGINT NOT NULL,
    session_seat_id BIGINT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'VALID' CHECK (status IN ('VALID','REFUNDED')),
    issued_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    refunded_at TIMESTAMPTZ,
    CONSTRAINT fk_ticket_item FOREIGN KEY (item_id,order_id,session_seat_id)
        REFERENCES booking_item (item_id,order_id,session_seat_id) ON DELETE RESTRICT,
    CONSTRAINT ck_ticket_refund_shape CHECK (
        (status='VALID' AND refunded_at IS NULL) OR
        (status='REFUNDED' AND refunded_at IS NOT NULL AND refunded_at>=issued_at)
    )
);
CREATE UNIQUE INDEX uq_ticket_valid_inventory ON ticket (session_seat_id) WHERE status='VALID';
CREATE INDEX ix_ticket_order ON ticket (order_id,ticket_id);

CREATE TABLE refund_attempt (
    refund_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    refund_no VARCHAR(40) NOT NULL UNIQUE,
    payment_id BIGINT NOT NULL,
    order_id BIGINT NOT NULL,
    request_id UUID NOT NULL,
    amount NUMERIC(14,2) NOT NULL,
    reason VARCHAR(200) NOT NULL CHECK (length(btrim(reason))>0),
    status VARCHAR(20) NOT NULL CHECK (status IN ('SUCCEEDED','FAILED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT uq_refund_request UNIQUE (order_id,request_id),
    CONSTRAINT fk_refund_payment FOREIGN KEY (payment_id,order_id,amount)
        REFERENCES payment_attempt (payment_id,order_id,amount) ON DELETE RESTRICT,
    CONSTRAINT ck_refund_amount CHECK (amount>=0 AND amount<1000000000000)
);
CREATE UNIQUE INDEX uq_refund_success_order ON refund_attempt (order_id) WHERE status='SUCCEEDED';

COMMENT ON TABLE payment_attempt IS '本地同步模拟支付尝试；每单最多成功一次，失败可以使用新请求编号重试';
COMMENT ON TABLE refund_attempt IS '本地同步整单全额模拟退款；失败不改变订单、票或库存';
COMMENT ON TABLE ticket IS '电子票记录；一张订单明细对应一张票，无检票或核销功能';
COMMENT ON TABLE booking_order IS '订单：待支付、取消、过期、已支付、已全额退款';
COMMENT ON TABLE session_seat IS '库存：AVAILABLE 可售，LOCKED 待支付，SOLD 已付款；退款释放库存';
COMMIT;
