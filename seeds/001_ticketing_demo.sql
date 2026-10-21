-- 演示数据：重复执行不重复新增，不改已有价格或状态。
-- 同名场馆/活动存在歧义或已有记录不符合演示条件时，明确报错而不是擅自修改。
BEGIN;
SET LOCAL search_path = public;
SET LOCAL lock_timeout = '5s';
DO $$
DECLARE
    v_organizer BIGINT;
    v_venue BIGINT;
    v_activity BIGINT;
    v_count BIGINT;
BEGIN
    INSERT INTO app_user (email, display_name, role)
    VALUES ('organizer@example.test', '校园活动主办方', 'ORGANIZER')
    ON CONFLICT (lower(btrim(email))) DO NOTHING;
    SELECT user_id INTO STRICT v_organizer FROM app_user
    WHERE lower(btrim(email)) = 'organizer@example.test'
      AND role IN ('ORGANIZER', 'ADMIN') AND status = 'ACTIVE';

    SELECT count(*), min(venue_id) INTO v_count, v_venue FROM venue
    WHERE name = '校园大礼堂' AND address = '学校主校区';
    IF v_count > 1 THEN RAISE EXCEPTION '存在多个同名同地址的校园大礼堂，请先明确演示场馆'; END IF;
    IF v_count = 0 THEN
        INSERT INTO venue (name, address) VALUES ('校园大礼堂', '学校主校区')
        RETURNING venue_id INTO v_venue;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM venue WHERE venue_id = v_venue AND status = 'ACTIVE') THEN
        RAISE EXCEPTION '演示场馆已停用，请先检查现有数据';
    END IF;

    INSERT INTO seat (venue_id, seat_code, row_label, seat_number)
    VALUES (v_venue, 'A1', 'A', 1), (v_venue, 'A2', 'A', 2)
    ON CONFLICT (venue_id, seat_code) DO NOTHING;
    SELECT count(*) INTO v_count FROM seat
    WHERE venue_id = v_venue AND status = 'ACTIVE' AND row_label = 'A'
      AND ((seat_code = 'A1' AND seat_number = 1) OR (seat_code = 'A2' AND seat_number = 2));
    IF v_count <> 2 THEN RAISE EXCEPTION '现有 A1/A2 的位置或启用状态不符合样例，不自动覆盖'; END IF;

    SELECT count(*), min(activity_id) INTO v_count, v_activity FROM activity
    WHERE title = '校园音乐会' AND organizer_id = v_organizer;
    IF v_count > 1 THEN RAISE EXCEPTION '同一主办方有多个校园音乐会，请先明确演示活动'; END IF;
    IF v_count = 0 THEN
        INSERT INTO activity (title, description, organizer_id)
        VALUES ('校园音乐会', '学生乐队与合唱团演出', v_organizer)
        RETURNING activity_id INTO v_activity;
    END IF;

    INSERT INTO activity_session (activity_id, venue_id, start_time, end_time)
    VALUES
        (v_activity, v_venue, '2026-10-30 19:00:00+08', '2026-10-30 21:00:00+08'),
        (v_activity, v_venue, '2026-10-31 19:00:00+08', '2026-10-31 21:00:00+08')
    ON CONFLICT (activity_id, venue_id, start_time) DO NOTHING;
    SELECT count(*) INTO v_count FROM activity_session
    WHERE activity_id = v_activity AND venue_id = v_venue
      AND ((start_time = '2026-10-30 19:00:00+08' AND end_time = '2026-10-30 21:00:00+08')
        OR (start_time = '2026-10-31 19:00:00+08' AND end_time = '2026-10-31 21:00:00+08'));
    IF v_count <> 2 THEN RAISE EXCEPTION '现有样例场次结束时间与计划不符，不自动覆盖'; END IF;

    INSERT INTO session_seat (session_id, seat_id, venue_id, price)
    SELECT s.session_id, p.seat_id, s.venue_id,
           CASE p.seat_code WHEN 'A1' THEN 100.00 ELSE 120.00 END
    FROM activity_session s
    JOIN seat p ON p.venue_id = s.venue_id
    WHERE s.activity_id = v_activity AND s.venue_id = v_venue
      AND s.start_time IN ('2026-10-30 19:00:00+08', '2026-10-31 19:00:00+08')
      AND p.seat_code IN ('A1', 'A2')
    ON CONFLICT (session_id, seat_id) DO NOTHING;
    RAISE NOTICE '样例已就绪：复用已有场馆和座位，配置两个场次、每场两个库存；不覆盖已有库存';
END;
$$;
COMMIT;
