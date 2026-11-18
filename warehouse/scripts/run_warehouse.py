#!/usr/bin/env python3
"""数仓构建编排：ODS 抽取 -> DWD 装载 -> 对账校验。

用法（需要管理员数据库账号，因为要创建 schema）：
    python warehouse/scripts/run_warehouse.py                # 全流程
    python warehouse/scripts/run_warehouse.py --note "周报"   # 给批次加备注
    python warehouse/scripts/run_warehouse.py --verify-only   # 只对账，不抽取

约定：
    - ODS 每次抽取是一个全量快照批次，只追加不修改；
    - DWD 每次全量重建，只消费最新 status='DONE' 的批次；
    - 任何一步失败都整体回滚，不会留下半成品批次。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg

WAREHOUSE_ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = WAREHOUSE_ROOT / "sql"

# ODS 快照的源表与列清单（与 migrations 定义逐列对应，勿随意调整顺序）。
SOURCE_TABLES: dict[str, tuple[str, ...]] = {
    "app_user": ("user_id", "email", "display_name", "role", "status", "created_at", "updated_at"),
    "venue": ("venue_id", "name", "address", "status", "created_at", "updated_at"),
    "seat": ("seat_id", "venue_id", "seat_code", "row_label", "seat_number", "status", "created_at", "updated_at"),
    "activity": ("activity_id", "title", "description", "organizer_id", "status", "created_at", "updated_at"),
    "activity_session": ("session_id", "activity_id", "venue_id", "start_time", "end_time", "status", "created_at", "updated_at"),
    "session_seat": ("session_seat_id", "session_id", "seat_id", "venue_id", "price", "status", "created_at", "updated_at"),
    "booking_order": ("order_id", "order_no", "user_id", "session_id", "request_id", "total_amount", "status", "expires_at", "created_at", "updated_at"),
    "booking_item": ("item_id", "order_id", "session_id", "session_seat_id", "unit_price", "created_at"),
    "order_status_history": ("history_id", "order_id", "from_status", "to_status", "reason", "actor_user_id", "changed_at"),
    "payment_attempt": ("payment_id", "payment_no", "order_id", "request_id", "amount", "provider", "status", "created_at"),
    "ticket": ("ticket_id", "ticket_no", "item_id", "order_id", "session_seat_id", "status", "issued_at", "refunded_at"),
    "refund_attempt": ("refund_id", "refund_no", "payment_id", "order_id", "request_id", "amount", "reason", "status", "created_at"),
}


def _run_sql_file(conn: psycopg.Connection, path: Path) -> None:
    conn.execute(path.read_text(encoding="utf-8"))


def extract(conn: psycopg.Connection, note: str) -> tuple[int, dict[str, int]]:
    """全量快照抽取：登记批次 -> 逐表复制 -> 标记完成。"""
    batch_id = conn.execute(
        "INSERT INTO ods.extract_batch (note) VALUES (%s) RETURNING batch_id", (note,)
    ).fetchone()[0]

    extracted_at = conn.execute("SELECT statement_timestamp()").fetchone()[0]
    counts: dict[str, int] = {}
    for table, columns in SOURCE_TABLES.items():
        column_list = ", ".join(columns)
        result = conn.execute(
            f"INSERT INTO ods.{table} (_batch_id, _extracted_at, {column_list}) "
            f"SELECT %s, %s, {column_list} FROM public.{table}",
            (batch_id, extracted_at),
        )
        counts[table] = result.rowcount if result.rowcount and result.rowcount > 0 else 0

    total = sum(counts.values())
    conn.execute(
        "UPDATE ods.extract_batch SET status = 'DONE', finished_at = statement_timestamp(), "
        "source_rows = %s WHERE batch_id = %s",
        (total, batch_id),
    )
    return batch_id, counts


def build_dwd(conn: psycopg.Connection) -> None:
    _run_sql_file(conn, SQL_DIR / "02_dwd_ddl.sql")
    _run_sql_file(conn, SQL_DIR / "03_dwd_load.sql")


def verify(conn: psycopg.Connection) -> list[tuple[str, bool, str]]:
    """对账：DWD 与 ODS 最新批次必须完全一致，任何一项不平视为构建失败。"""
    checks = [
        ("订单行数一致",
         "SELECT (SELECT count(*) FROM dwd.fact_order) = "
         "      (SELECT count(*) FROM ods.booking_order WHERE _batch_id = (SELECT max(batch_id) FROM ods.extract_batch WHERE status='DONE'))",
         "fact_order 行数应等于 ODS booking_order 行数"),
        ("订单金额一致",
         "SELECT (SELECT COALESCE(sum(total_amount),0) FROM dwd.fact_order) = "
         "      (SELECT COALESCE(sum(total_amount),0) FROM ods.booking_order WHERE _batch_id = (SELECT max(batch_id) FROM ods.extract_batch WHERE status='DONE'))",
         "sum(total_amount) 应完全相等"),
        ("订单明细数一致",
         "SELECT (SELECT COALESCE(sum(item_count),0) FROM dwd.fact_order) = "
         "      (SELECT count(*) FROM ods.booking_item WHERE _batch_id = (SELECT max(batch_id) FROM ods.extract_batch WHERE status='DONE'))",
         "sum(item_count) 应等于 ODS booking_item 行数"),
        ("成功支付金额 = 已付+已退订单金额",
         "SELECT (SELECT COALESCE(sum(amount),0) FROM dwd.fact_payment WHERE payment_status='SUCCEEDED') = "
         "      (SELECT COALESCE(sum(total_amount),0) FROM dwd.fact_order WHERE order_status IN ('PAID','REFUNDED'))",
         "支付事实与订单事实的金额闭环"),
        ("票行数一致",
         "SELECT (SELECT count(*) FROM dwd.fact_ticket) = "
         "      (SELECT count(*) FROM ods.ticket WHERE _batch_id = (SELECT max(batch_id) FROM ods.extract_batch WHERE status='DONE'))",
         "fact_ticket 行数应等于 ODS ticket 行数"),
        ("已付订单都有 paid_at",
         "SELECT count(*) = 0 FROM dwd.fact_order "
         " WHERE order_status IN ('PAID','REFUNDED') AND paid_at IS NULL",
         "PAID/REFUNDED 订单不允许缺失支付完成时间"),
        ("维度表无孤儿事实（订单）",
         "SELECT count(*) = 0 FROM dwd.fact_order f "
         " WHERE NOT EXISTS (SELECT 1 FROM dwd.dim_user u WHERE u.user_id = f.user_id) "
         "    OR NOT EXISTS (SELECT 1 FROM dwd.dim_session s WHERE s.session_id = f.session_id)",
         "每条订单事实必须能关联到用户与场次维度"),
        ("票事实可关联座位",
         "SELECT count(*) = 0 FROM dwd.fact_ticket t WHERE t.seat_code IS NULL OR t.session_id IS NULL",
         "seat_code 缺失说明 item/seat 关联断裂"),
    ]
    results = []
    for name, sql, detail in checks:
        ok = conn.execute(sql).fetchone()[0]
        results.append((name, bool(ok), detail))
    return results


def report(batch_id: int | None, counts: dict[str, int] | None, results: list[tuple[str, bool, str]]) -> int:
    if batch_id is not None and counts is not None:
        print(f"ODS 批次 {batch_id} 抽取完成")
        for table, rows in counts.items():
            print(f"  {table:<22}{rows:>7}")
        total = sum(counts.values())
        print(f"  {'合计':<22}{total:>7}")
        print()

    failed = 0
    print("DWD 对账：")
    for name, ok, detail in results:
        print(f"  [{'OK' if ok else '!!'}] {name}（{detail}）")
        failed += 0 if ok else 1
    return failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="构建 ODS/DWD 数仓层并对账")
    parser.add_argument("--note", default="", help="批次备注")
    parser.add_argument("--verify-only", action="store_true", help="跳过抽取与装载，只做对账")
    args = parser.parse_args(argv)

    with psycopg.connect() as conn:
        if not args.verify_only:
            _run_sql_file(conn, SQL_DIR / "01_ods_ddl.sql")
            batch_id, counts = extract(conn, args.note)
            build_dwd(conn)
        else:
            row = conn.execute(
                "SELECT batch_id FROM ods.extract_batch WHERE status='DONE' ORDER BY batch_id DESC LIMIT 1"
            ).fetchone()
            if row is None:
                print("还没有可对账的批次，请先执行完整构建")
                return 2
            batch_id, counts = row[0], None
        results = verify(conn)
        failed = report(batch_id, counts, results)
        if failed:
            raise SystemExit(f"对账失败 {failed} 项，事务已回滚")
    print("\n全部通过：ODS 快照已入库，DWD 已重建并与 ODS 对平。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
