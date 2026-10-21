-- 约束集成验证：所有样例行在末尾回滚，不写入长期样例数据。
-- Identity 序列不会随事务回滚，因此执行后主键存在空号属于正常现象。
BEGIN;
DO $$
DECLARE
    v_user BIGINT;
    v_venue_a BIGINT;
    v_venue_b BIGINT;
    v_activity BIGINT;
    v_seat BIGINT;
    v_constraint TEXT;
    v_updated TIMESTAMPTZ;
BEGIN
    INSERT INTO app_user (email, display_name, role)
    VALUES ('schema-check@example.test', '建表验证主办方', 'ORGANIZER') RETURNING user_id INTO v_user;
    INSERT INTO venue (name, address) VALUES ('验证场馆 A', '测试地址 A') RETURNING venue_id INTO v_venue_a;
    INSERT INTO venue (name, address) VALUES ('验证场馆 B', '测试地址 B') RETURNING venue_id INTO v_venue_b;
    INSERT INTO seat (venue_id, seat_code, row_label, seat_number, updated_at)
    VALUES (v_venue_a, 'A1', 'A', 1, '2000-01-01T00:00:00Z') RETURNING seat_id INTO v_seat;
    INSERT INTO seat (venue_id, seat_code, row_label, seat_number)
    VALUES (v_venue_b, 'A1', 'A', 1);
    INSERT INTO activity (title, organizer_id) VALUES ('验证音乐会', v_user) RETURNING activity_id INTO v_activity;
    INSERT INTO activity_session (activity_id, venue_id, start_time, end_time)
    VALUES (v_activity, v_venue_a, '2026-10-24T19:00:00+08:00', '2026-10-24T21:00:00+08:00');
    RAISE NOTICE 'PASS: 关联数据可以正常创建，不同场馆允许同名 A1 座位';

    BEGIN
        INSERT INTO seat (venue_id, seat_code, row_label, seat_number) VALUES (v_venue_a, 'A1', 'B', 2);
        RAISE EXCEPTION 'FAIL: 同场馆重复座位编号未被拦截';
    EXCEPTION WHEN unique_violation THEN
        GET STACKED DIAGNOSTICS v_constraint = CONSTRAINT_NAME;
        IF v_constraint <> 'uq_seat_venue_code' THEN RAISE; END IF;
        RAISE NOTICE 'PASS: 同场馆重复座位编号被拦截';
    END;

    BEGIN
        INSERT INTO seat (venue_id, seat_code, row_label, seat_number) VALUES (v_venue_a, 'OTHER', 'A', 1);
        RAISE EXCEPTION 'FAIL: 同一物理位置的重复座位未被拦截';
    EXCEPTION WHEN unique_violation THEN
        GET STACKED DIAGNOSTICS v_constraint = CONSTRAINT_NAME;
        IF v_constraint <> 'uq_seat_venue_position' THEN RAISE; END IF;
        RAISE NOTICE 'PASS: 更换座位编号也不能重复创建同一位置';
    END;

    BEGIN
        INSERT INTO app_user (email, display_name) VALUES (' SCHEMA-CHECK@EXAMPLE.TEST ', '重复账号');
        RAISE EXCEPTION 'FAIL: 邮箱大小写与空格重复未被拦截';
    EXCEPTION WHEN unique_violation THEN
        GET STACKED DIAGNOSTICS v_constraint = CONSTRAINT_NAME;
        IF v_constraint <> 'ux_app_user_email' THEN RAISE; END IF;
        RAISE NOTICE 'PASS: 邮箱大小写与首尾空格不会产生重复账号';
    END;

    BEGIN
        INSERT INTO activity_session (activity_id, venue_id, start_time, end_time)
        VALUES (v_activity, v_venue_a, '2026-10-25T21:00:00+08:00', '2026-10-25T19:00:00+08:00');
        RAISE EXCEPTION 'FAIL: 结束早于开始的场次未被拦截';
    EXCEPTION WHEN check_violation THEN
        GET STACKED DIAGNOSTICS v_constraint = CONSTRAINT_NAME;
        IF v_constraint <> 'ck_activity_session_time' THEN RAISE; END IF;
        RAISE NOTICE 'PASS: 不合法的场次时间被拦截';
    END;

    BEGIN
        INSERT INTO seat (venue_id, seat_code, row_label, seat_number) VALUES (-1, 'A1', 'A', 1);
        RAISE EXCEPTION 'FAIL: 不存在场馆的座位未被拦截';
    EXCEPTION WHEN foreign_key_violation THEN
        GET STACKED DIAGNOSTICS v_constraint = CONSTRAINT_NAME;
        IF v_constraint <> 'fk_seat_venue' THEN RAISE; END IF;
        RAISE NOTICE 'PASS: 座位不能关联不存在的场馆';
    END;

    BEGIN
        DELETE FROM app_user WHERE user_id = v_user;
        RAISE EXCEPTION 'FAIL: 已有关联活动的主办用户被删除';
    EXCEPTION WHEN foreign_key_violation THEN
        GET STACKED DIAGNOSTICS v_constraint = CONSTRAINT_NAME;
        IF v_constraint <> 'fk_activity_organizer' THEN RAISE; END IF;
        RAISE NOTICE 'PASS: 有关联活动的用户不能直接删除';
    END;

    UPDATE seat SET status = 'INACTIVE' WHERE seat_id = v_seat RETURNING updated_at INTO v_updated;
    IF v_updated <= '2000-01-01T00:00:00Z'::timestamptz THEN
        RAISE EXCEPTION 'FAIL: updated_at 没有自动更新';
    END IF;
    RAISE NOTICE 'PASS: 更新触发器自动维护修改时间';
END;
$$;
ROLLBACK;
