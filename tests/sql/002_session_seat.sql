-- 隔离的样例行在末尾回滚；不会修改已有演示库存。Identity 编号可能前进。
BEGIN;
DO $$
DECLARE
    v_user BIGINT;
    v_venue_a BIGINT;
    v_venue_b BIGINT;
    v_seat_a BIGINT;
    v_seat_b BIGINT;
    v_activity BIGINT;
    v_session_a BIGINT;
    v_session_b BIGINT;
    v_constraint TEXT;
    v_price NUMERIC;
BEGIN
    INSERT INTO app_user (email, display_name, role)
    VALUES ('session-seat-check@example.test', '库存测试主办方', 'ORGANIZER') RETURNING user_id INTO v_user;
    INSERT INTO venue (name, address) VALUES ('库存验证场馆 A', '测试 A') RETURNING venue_id INTO v_venue_a;
    INSERT INTO venue (name, address) VALUES ('库存验证场馆 B', '测试 B') RETURNING venue_id INTO v_venue_b;
    INSERT INTO seat (venue_id, seat_code, row_label, seat_number)
    VALUES (v_venue_a, 'A1', 'A', 1) RETURNING seat_id INTO v_seat_a;
    INSERT INTO seat (venue_id, seat_code, row_label, seat_number)
    VALUES (v_venue_b, 'A1', 'A', 1) RETURNING seat_id INTO v_seat_b;
    INSERT INTO activity (title, organizer_id) VALUES ('库存验证活动', v_user) RETURNING activity_id INTO v_activity;
    INSERT INTO activity_session (activity_id, venue_id, start_time, end_time)
    VALUES (v_activity, v_venue_a, '2026-11-01T19:00:00+08', '2026-11-01T21:00:00+08') RETURNING session_id INTO v_session_a;
    INSERT INTO activity_session (activity_id, venue_id, start_time, end_time)
    VALUES (v_activity, v_venue_a, '2026-11-02T19:00:00+08', '2026-11-02T21:00:00+08') RETURNING session_id INTO v_session_b;
    INSERT INTO session_seat (session_id, seat_id, venue_id, price)
    VALUES (v_session_a, v_seat_a, v_venue_a, 100), (v_session_b, v_seat_a, v_venue_a, 120);
    UPDATE session_seat SET price = 150 WHERE session_id = v_session_a AND seat_id = v_seat_a;
    SELECT price INTO v_price FROM session_seat WHERE session_id = v_session_b AND seat_id = v_seat_a;
    IF v_price <> 120 THEN RAISE EXCEPTION 'FAIL: 修改某场次价格影响了另一个场次'; END IF;
    RAISE NOTICE 'PASS: 同一个物理座位在不同场次独立配置和修改价格';

    BEGIN
        INSERT INTO session_seat (session_id, seat_id, venue_id, price) VALUES (v_session_a, v_seat_b, v_venue_a, 100);
        RAISE EXCEPTION 'FAIL: 座位所属场馆与库存场馆不符仍然写入';
    EXCEPTION WHEN foreign_key_violation THEN
        GET STACKED DIAGNOSTICS v_constraint = CONSTRAINT_NAME;
        IF v_constraint <> 'fk_session_seat_seat_venue' THEN RAISE; END IF;
        RAISE NOTICE 'PASS: 其他场馆的座位不能混入本场馆库存';
    END;
    BEGIN
        INSERT INTO session_seat (session_id, seat_id, venue_id, price) VALUES (v_session_a, v_seat_b, v_venue_b, 100);
        RAISE EXCEPTION 'FAIL: 修改库存场馆可绕过场次场馆校验';
    EXCEPTION WHEN foreign_key_violation THEN
        GET STACKED DIAGNOSTICS v_constraint = CONSTRAINT_NAME;
        IF v_constraint <> 'fk_session_seat_session_venue' THEN RAISE; END IF;
        RAISE NOTICE 'PASS: 修改库存场馆也不能绕过场次关联校验';
    END;
    BEGIN
        INSERT INTO session_seat (session_id, seat_id, venue_id, price) VALUES (v_session_a, v_seat_a, v_venue_a, 200);
        RAISE EXCEPTION 'FAIL: 同场次同座位重复配置';
    EXCEPTION WHEN unique_violation THEN
        GET STACKED DIAGNOSTICS v_constraint = CONSTRAINT_NAME;
        IF v_constraint <> 'uq_session_seat' THEN RAISE; END IF;
        RAISE NOTICE 'PASS: 同场次同座位只能配置一条库存';
    END;
    BEGIN
        UPDATE session_seat SET price = -1 WHERE session_id = v_session_a AND seat_id = v_seat_a;
        RAISE EXCEPTION 'FAIL: 负票价未被拦截';
    EXCEPTION WHEN check_violation THEN
        GET STACKED DIAGNOSTICS v_constraint = CONSTRAINT_NAME;
        IF v_constraint <> 'ck_session_seat_price' THEN RAISE; END IF;
        RAISE NOTICE 'PASS: 负票价被拦截';
    END;
    BEGIN
        UPDATE session_seat SET status = 'LOCKED' WHERE session_id = v_session_a AND seat_id = v_seat_a;
        RAISE EXCEPTION 'FAIL: 缺少订单与截止时间仍允许锁座';
    EXCEPTION WHEN check_violation THEN
        GET STACKED DIAGNOSTICS v_constraint = CONSTRAINT_NAME;
        IF v_constraint NOT IN ('ck_session_seat_status', 'ck_inventory_lock_shape') THEN RAISE; END IF;
        RAISE NOTICE 'PASS: 不完整的锁座状态被数据库约束拦截';
    END;
END;
$$;
ROLLBACK;
