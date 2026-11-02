import argparse
import json
import os
import sys
import time

import psycopg

from .booking import BookingError, BookingService
from .db import connect
from .payment import PaymentService


def main():
    parser = argparse.ArgumentParser(description="票务订单、模拟支付、电子票与整单退款演示")
    subs = parser.add_subparsers(dest="command", required=True)
    subs.add_parser("init", help="管理员：应用迁移并配置受限应用账号")
    demo = subs.add_parser("demo", help="管理员：准备未来场次与两个普通用户")
    demo.add_argument("--start", help="可选固定开始时间，需包含时区")
    subs.add_parser("catalog", help="查看普通用户与可用场次库存")
    reserve = subs.add_parser("reserve", help="创建待支付订单并锁座")
    reserve.add_argument("--user", type=int, required=True)
    reserve.add_argument("--session", type=int, required=True)
    reserve.add_argument("--seats", type=int, nargs="+", required=True, help="session_seat_id，非物理 seat_id")
    reserve.add_argument("--request-id", help="UUID；相同用户的重试复用同一值")
    reserve.add_argument("--hold-seconds", type=int, default=600, help="本地演示 1～600 秒，默认 600")
    for name in ("cancel", "order"):
        command = subs.add_parser(name)
        command.add_argument("--user", type=int, required=True)
        command.add_argument("--order", type=int, required=True)
    for name in ("pay", "refund"):
        command = subs.add_parser(name, help="本地模拟支付" if name == "pay" else "开场前整单全额模拟退款")
        command.add_argument("--user", type=int, required=True)
        command.add_argument("--order", type=int, required=True)
        command.add_argument("--request-id", help="UUID；重试同一操作使用原值")
        command.add_argument("--outcome", choices=("SUCCEEDED", "FAILED"), default="SUCCEEDED")
        if name == "pay":
            command.add_argument("--amount", required=True, help="必须与订单总额一致，例如 220.00")
        else:
            command.add_argument("--reason", required=True, help="退款原因，1～200 字符")
    expire = subs.add_parser("expire", help="手动清理一批已过期待支付订单")
    expire.add_argument("--limit", type=int, default=100)
    worker = subs.add_parser("worker", help="周期执行过期清理")
    worker.add_argument("--interval", type=float, default=5)
    worker.add_argument("--once", action="store_true")
    args = parser.parse_args()
    try:
        service = BookingService(hold_seconds=getattr(args, "hold_seconds", 600))
        if args.command == "init":
            from .bootstrap import initialize
            result = initialize()
        elif args.command == "demo":
            from .bootstrap import seed_booking_demo
            with connect() as conn:
                result = seed_booking_demo(conn, start=args.start)
        elif args.command == "catalog":
            with connect() as conn, conn.transaction():
                result = {
                    "users": conn.execute("SELECT user_id,display_name FROM app_user WHERE role='CUSTOMER' AND status='ACTIVE' ORDER BY user_id").fetchall(),
                    "inventory": conn.execute("""SELECT ss.session_seat_id,ss.session_id,p.seat_code,a.title,
                        s.start_time AT TIME ZONE 'Asia/Shanghai' AS start_beijing,ss.price,ss.status,ss.locked_until
                        FROM session_seat ss JOIN seat p USING (seat_id)
                        JOIN activity_session s ON s.session_id=ss.session_id
                        JOIN activity a ON a.activity_id=s.activity_id
                        JOIN venue v ON v.venue_id=s.venue_id
                        WHERE s.status='ON_SALE' AND a.status='PUBLISHED' AND v.status='ACTIVE'
                        AND p.status='ACTIVE' AND s.start_time>clock_timestamp()
                        ORDER BY s.start_time,ss.session_id,p.seat_code""").fetchall(),
                }
        elif args.command == "reserve":
            result = service.reserve(args.user, args.session, args.seats, request_id=args.request_id)
        elif args.command == "cancel":
            result = service.cancel(args.user, args.order)
        elif args.command == "order":
            result = service.get(args.user, args.order)
        elif args.command == "pay":
            result = PaymentService().pay(args.user, args.order, args.amount,
                                         request_id=args.request_id, outcome=args.outcome)
        elif args.command == "refund":
            result = PaymentService().refund(args.user, args.order, reason=args.reason,
                                            request_id=args.request_id, outcome=args.outcome)
        elif args.command == "expire":
            result = {"expired_order_ids": service.expire(limit=args.limit)}
        else:
            if args.interval <= 0:
                raise BookingError("清理间隔必须大于 0")
            while True:
                try:
                    print(json.dumps({"expired_order_ids": service.expire()}, ensure_ascii=False), flush=True)
                except (BookingError, psycopg.Error) as exc:
                    # 不输出连接参数或密码；持续运行时保留本轮失败记录，下轮重试。
                    print(json.dumps({"error": str(exc) if isinstance(exc, BookingError) else type(exc).__name__}), file=sys.stderr, flush=True)
                    if args.once:
                        return 1
                if args.once:
                    return 0
                time.sleep(args.interval)
        print(json.dumps(result, default=str, ensure_ascii=False, indent=2))
        return 0
    except (BookingError, ValueError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    except psycopg.Error as exc:
        print(json.dumps({"error": type(exc).__name__, "message": "数据库操作失败，请检查服务日志和连接配置"}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
