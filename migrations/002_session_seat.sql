-- 在 001_core_tables.sql 之后执行一次，不重建已有基础表。
BEGIN;
SET LOCAL search_path = public;
SET LOCAL lock_timeout = '5s';

-- 组合外键需要对应的组合唯一键，保证库存的场次和座位属于同一场馆。
ALTER TABLE seat ADD CONSTRAINT uq_seat_id_venue UNIQUE (seat_id, venue_id);
ALTER TABLE activity_session ADD CONSTRAINT uq_session_id_venue UNIQUE (session_id, venue_id);

CREATE TABLE session_seat (
    session_seat_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id BIGINT NOT NULL,
    seat_id BIGINT NOT NULL,
    venue_id BIGINT NOT NULL,
    price NUMERIC(12, 2) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'AVAILABLE',
    created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    CONSTRAINT uq_session_seat UNIQUE (session_id, seat_id),
    CONSTRAINT fk_session_seat_session_venue FOREIGN KEY (session_id, venue_id)
        REFERENCES activity_session (session_id, venue_id) ON DELETE RESTRICT,
    CONSTRAINT fk_session_seat_seat_venue FOREIGN KEY (seat_id, venue_id)
        REFERENCES seat (seat_id, venue_id) ON DELETE RESTRICT,
    -- 上界配合非负检查排除非金额特殊值 NaN；精度本身也限制金额范围。
    CONSTRAINT ck_session_seat_price CHECK (price >= 0 AND price < 10000000000),
    -- 当前尚无订单表，只开放可售库存配置。订单迁移中扩展锁座与已售状态及其约束。
    CONSTRAINT ck_session_seat_status CHECK (status = 'AVAILABLE')
);
CREATE INDEX ix_session_seat_seat_venue ON session_seat (seat_id, venue_id);
CREATE TRIGGER trg_session_seat_updated_at
BEFORE UPDATE ON session_seat FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
COMMENT ON TABLE session_seat IS '场次座位库存与独立票价；当前只开放 AVAILABLE，订单阶段补充锁座与售出状态';
COMMENT ON COLUMN session_seat.venue_id IS '通过两组组合外键保证场次与物理座位属于同一场馆';

COMMIT;
