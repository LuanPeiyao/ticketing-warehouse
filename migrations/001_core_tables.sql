-- 第一阶段基础表。只在尚未创建这些表的数据库中执行一次。
-- 使用 psql -v ON_ERROR_STOP=1 执行；任意语句失败时整次建表回滚。
BEGIN;
SET LOCAL search_path = public;
SET LOCAL lock_timeout = '5s';

-- DEFAULT 只负责插入时赋值；触发器负责更新时维护 updated_at。
CREATE FUNCTION public.set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at := statement_timestamp();
    RETURN NEW;
END;
$$;

-- 用户：先保存用户资料与业务角色，登录认证后续实现。
CREATE TABLE app_user (
    user_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email VARCHAR(254) NOT NULL,
    display_name VARCHAR(100) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'CUSTOMER',
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    CONSTRAINT ck_app_user_email_nonblank CHECK (length(btrim(email)) > 0),
    CONSTRAINT ck_app_user_name_nonblank CHECK (length(btrim(display_name)) > 0),
    CONSTRAINT ck_app_user_role CHECK (role IN ('CUSTOMER', 'ORGANIZER', 'ADMIN')),
    CONSTRAINT ck_app_user_status CHECK (status IN ('ACTIVE', 'INACTIVE'))
);
-- 邮箱首尾空格、大小写差异不产生第二个账号；邮箱格式由后续 API 校验。
CREATE UNIQUE INDEX ux_app_user_email ON app_user (lower(btrim(email)));
CREATE TRIGGER trg_app_user_updated_at
BEFORE UPDATE ON app_user FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
COMMENT ON TABLE app_user IS '用户资料与业务角色；不包含登录认证实现';

-- 场馆：座位数以后从有效座位配置得出，不重复维护容量计数。
CREATE TABLE venue (
    venue_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    address TEXT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    CONSTRAINT ck_venue_name_nonblank CHECK (length(btrim(name)) > 0),
    CONSTRAINT ck_venue_address_nonblank CHECK (length(btrim(address)) > 0),
    CONSTRAINT ck_venue_status CHECK (status IN ('ACTIVE', 'INACTIVE'))
);
CREATE TRIGGER trg_venue_updated_at
BEFORE UPDATE ON venue FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
COMMENT ON TABLE venue IS '场馆基本资料';

-- 物理座位：保存位置和是否启用，不保存某场次的价格或售卖状态。
CREATE TABLE seat (
    seat_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    venue_id BIGINT NOT NULL,
    seat_code VARCHAR(32) NOT NULL,
    row_label VARCHAR(16) NOT NULL,
    seat_number INTEGER NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    CONSTRAINT fk_seat_venue FOREIGN KEY (venue_id) REFERENCES venue (venue_id) ON DELETE RESTRICT,
    CONSTRAINT uq_seat_venue_code UNIQUE (venue_id, seat_code),
    CONSTRAINT uq_seat_venue_position UNIQUE (venue_id, row_label, seat_number),
    CONSTRAINT ck_seat_code CHECK (length(seat_code) > 0 AND seat_code = upper(btrim(seat_code))),
    CONSTRAINT ck_seat_row CHECK (length(row_label) > 0 AND row_label = upper(btrim(row_label))),
    CONSTRAINT ck_seat_number CHECK (seat_number > 0),
    CONSTRAINT ck_seat_status CHECK (status IN ('ACTIVE', 'INACTIVE'))
);
CREATE TRIGGER trg_seat_updated_at
BEFORE UPDATE ON seat FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
COMMENT ON TABLE seat IS '场馆物理座位；场次价格和库存由后续 session_seat 表保存';

-- 活动：一场校园音乐会可以安排多个场次。
CREATE TABLE activity (
    activity_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title VARCHAR(200) NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    organizer_id BIGINT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'DRAFT',
    created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    CONSTRAINT fk_activity_organizer FOREIGN KEY (organizer_id) REFERENCES app_user (user_id) ON DELETE RESTRICT,
    CONSTRAINT ck_activity_title_nonblank CHECK (length(btrim(title)) > 0),
    CONSTRAINT ck_activity_status CHECK (status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED'))
);
CREATE INDEX ix_activity_organizer ON activity (organizer_id);
CREATE TRIGGER trg_activity_updated_at
BEFORE UPDATE ON activity FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
COMMENT ON TABLE activity IS '活动主题及主办用户；主办权限由后续应用层校验';

-- 场次：活动在某场馆的一次具体安排。
CREATE TABLE activity_session (
    session_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    activity_id BIGINT NOT NULL,
    venue_id BIGINT NOT NULL,
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'SCHEDULED',
    created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    CONSTRAINT fk_activity_session_activity FOREIGN KEY (activity_id) REFERENCES activity (activity_id) ON DELETE RESTRICT,
    CONSTRAINT fk_activity_session_venue FOREIGN KEY (venue_id) REFERENCES venue (venue_id) ON DELETE RESTRICT,
    CONSTRAINT uq_activity_session_schedule UNIQUE (activity_id, venue_id, start_time),
    CONSTRAINT ck_activity_session_time CHECK (end_time > start_time),
    CONSTRAINT ck_activity_session_status CHECK (status IN ('SCHEDULED', 'ON_SALE', 'CLOSED', 'CANCELLED', 'FINISHED'))
);
CREATE INDEX ix_activity_session_activity_time ON activity_session (activity_id, start_time);
CREATE INDEX ix_activity_session_venue_time ON activity_session (venue_id, start_time);
CREATE TRIGGER trg_activity_session_updated_at
BEFORE UPDATE ON activity_session FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
COMMENT ON TABLE activity_session IS '活动的具体场次；场馆时间区间冲突与状态流转由后续事务流程处理';

COMMIT;
