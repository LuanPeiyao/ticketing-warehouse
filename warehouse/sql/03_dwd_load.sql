-- DWD 装载：消费 ODS 最新完成批次，全量重建 DWD 层。
-- 幂等性：TRUNCATE + INSERT，可重复执行；失败时脚本层整体回滚。
-- 前置条件：ods.extract_batch 中存在 status='DONE' 的批次。
BEGIN;
SET LOCAL lock_timeout = '30s';

-- 最新完成批次（本文件所有语句共用）。
DROP TABLE IF EXISTS _latest_batch;
CREATE TEMP TABLE _latest_batch AS
SELECT batch_id FROM ods.extract_batch WHERE status = 'DONE' ORDER BY batch_id DESC LIMIT 1;

-- ============ 维度装载 ============

TRUNCATE dwd.dim_user;
INSERT INTO dwd.dim_user
SELECT u.user_id,
       u.email,
       lower(split_part(u.email, '@', 2)) AS email_domain,
       u.display_name,
       u.role,
       u.status,
       u.created_at
FROM ods.app_user u
WHERE u._batch_id = (SELECT batch_id FROM _latest_batch);

TRUNCATE dwd.dim_venue;
INSERT INTO dwd.dim_venue
SELECT v.venue_id,
       v.name,
       v.address,
       v.status,
       (SELECT count(*) FROM ods.seat s
        WHERE s.venue_id = v.venue_id
          AND s._batch_id = (SELECT batch_id FROM _latest_batch)) AS seat_count,
       v.created_at
FROM ods.venue v
WHERE v._batch_id = (SELECT batch_id FROM _latest_batch);

TRUNCATE dwd.dim_activity;
INSERT INTO dwd.dim_activity
SELECT a.activity_id,
       a.title,
       a.organizer_id,
       COALESCE(o.display_name, '(未知)') AS organizer_name,
       a.status,
       a.created_at
FROM ods.activity a
LEFT JOIN ods.app_user o
       ON o.user_id = a.organizer_id
      AND o._batch_id = (SELECT batch_id FROM _latest_batch)
WHERE a._batch_id = (SELECT batch_id FROM _latest_batch);

TRUNCATE dwd.dim_session;
INSERT INTO dwd.dim_session
SELECT s.session_id,
       s.activity_id,
       COALESCE(a.title, '(未知)')       AS activity_title,
       s.venue_id,
       COALESCE(v.name, '(未知)')        AS venue_name,
       s.start_time,
       s.end_time,
       (SELECT floor(EXTRACT(EPOCH FROM (s.end_time - s.start_time)) / 60))::integer AS duration_minutes,
       s.status                          AS session_status,
       (s.start_time AT TIME ZONE 'Asia/Shanghai')::date            AS local_date,
       EXTRACT(HOUR FROM s.start_time AT TIME ZONE 'Asia/Shanghai')::smallint AS local_start_hour,
       EXTRACT(DOW  FROM s.start_time AT TIME ZONE 'Asia/Shanghai')::smallint AS day_of_week,
       (EXTRACT(DOW FROM s.start_time AT TIME ZONE 'Asia/Shanghai') IN (0, 6)) AS is_weekend
FROM ods.activity_session s
LEFT JOIN ods.activity a
       ON a.activity_id = s.activity_id
      AND a._batch_id = (SELECT batch_id FROM _latest_batch)
LEFT JOIN ods.venue v
       ON v.venue_id = s.venue_id
      AND v._batch_id = (SELECT batch_id FROM _latest_batch)
WHERE s._batch_id = (SELECT batch_id FROM _latest_batch);

-- ============ 事实装载 ============

TRUNCATE dwd.fact_order;
INSERT INTO dwd.fact_order
WITH pay AS (
    SELECT p.order_id,
           count(*)                                   AS attempt_count,
           count(*) FILTER (WHERE p.status = 'FAILED') AS failed_count,
           min(p.created_at)                          AS first_payment_at,
           max(p.created_at) FILTER (WHERE p.status = 'SUCCEEDED') AS paid_at
    FROM ods.payment_attempt p
    WHERE p._batch_id = (SELECT batch_id FROM _latest_batch)
    GROUP BY p.order_id
),
ref AS (
    SELECT r.order_id,
           count(*)            AS refund_count,
           max(r.created_at) FILTER (WHERE r.status = 'SUCCEEDED') AS refunded_at
    FROM ods.refund_attempt r
    WHERE r._batch_id = (SELECT batch_id FROM _latest_batch)
    GROUP BY r.order_id
),
items AS (
    SELECT i.order_id, count(*) AS item_count
    FROM ods.booking_item i
    WHERE i._batch_id = (SELECT batch_id FROM _latest_batch)
    GROUP BY i.order_id
),
-- 流转到最终状态的那条历史，作为终态原因。
final_reason AS (
    SELECT DISTINCT ON (h.order_id)
           h.order_id, h.reason
    FROM ods.order_status_history h
    JOIN ods.booking_order o
      ON o.order_id = h.order_id
     AND o._batch_id = (SELECT batch_id FROM _latest_batch)
    WHERE h._batch_id = (SELECT batch_id FROM _latest_batch)
      AND h.to_status = o.status
    ORDER BY h.order_id, h.changed_at DESC
)
SELECT o.order_id,
       o.order_no,
       o.user_id,
       o.session_id,
       o.request_id,
       o.total_amount,
       o.status                        AS order_status,
       o.created_at                    AS order_created_at,
       o.expires_at,
       COALESCE(items.item_count, 0)   AS item_count,
       COALESCE(pay.attempt_count, 0)  AS payment_attempt_count,
       COALESCE(pay.failed_count, 0)   AS payment_failed_count,
       pay.first_payment_at,
       pay.paid_at,
       CASE WHEN pay.paid_at IS NOT NULL
            THEN round(EXTRACT(EPOCH FROM (pay.paid_at - o.created_at)) / 60, 2)
       END                             AS hold_minutes,
       COALESCE(ref.refund_count, 0)   AS refund_count,
       ref.refunded_at,
       CASE WHEN ref.refunded_at IS NOT NULL AND pay.paid_at IS NOT NULL
            THEN round(EXTRACT(EPOCH FROM (ref.refunded_at - pay.paid_at)) / 60, 2)
       END                             AS refund_delay_minutes,
       fr.reason                       AS final_reason
FROM ods.booking_order o
LEFT JOIN pay          ON pay.order_id = o.order_id
LEFT JOIN ref          ON ref.order_id = o.order_id
LEFT JOIN items        ON items.order_id = o.order_id
LEFT JOIN final_reason fr ON fr.order_id = o.order_id
WHERE o._batch_id = (SELECT batch_id FROM _latest_batch);

TRUNCATE dwd.fact_payment;
INSERT INTO dwd.fact_payment
SELECT p.payment_id,
       p.payment_no,
       p.order_id,
       p.amount,
       p.status  AS payment_status,
       p.provider,
       p.created_at AS attempted_at
FROM ods.payment_attempt p
WHERE p._batch_id = (SELECT batch_id FROM _latest_batch);

TRUNCATE dwd.fact_refund;
INSERT INTO dwd.fact_refund
SELECT r.refund_id,
       r.refund_no,
       r.order_id,
       r.payment_id,
       r.amount,
       r.status  AS refund_status,
       r.reason,
       r.created_at AS attempted_at
FROM ods.refund_attempt r
WHERE r._batch_id = (SELECT batch_id FROM _latest_batch);

TRUNCATE dwd.fact_ticket;
INSERT INTO dwd.fact_ticket
SELECT t.ticket_id,
       t.ticket_no,
       t.order_id,
       i.session_id,
       t.session_seat_id,
       s.seat_code,
       s.row_label,
       t.status   AS ticket_status,
       t.issued_at,
       t.refunded_at
FROM ods.ticket t
LEFT JOIN ods.booking_item i
       ON i.item_id = t.item_id
      AND i._batch_id = (SELECT batch_id FROM _latest_batch)
LEFT JOIN ods.seat s
       ON s.seat_id = (SELECT ss.seat_id FROM ods.session_seat ss
                       WHERE ss.session_seat_id = t.session_seat_id
                         AND ss._batch_id = (SELECT batch_id FROM _latest_batch))
      AND s._batch_id = (SELECT batch_id FROM _latest_batch)
WHERE t._batch_id = (SELECT batch_id FROM _latest_batch);

COMMIT;
