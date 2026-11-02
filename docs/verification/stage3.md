# 第三阶段验证记录

验证日期：2026-11-02。平台：macOS / Apple Silicon，Docker Compose，PostgreSQL 17.11，Python 3.12.16，Psycopg 3.2.13。

- 19 项 Python 数据库集成测试通过，另有 14 项既有 SQL 场景通过。
- 两个独立数据库连接真实重叠，观察到 Lock / transactionid 等待及阻塞者编号，详细证据见日志。
- 同座位竞争仅一个订单成功；相同 request_id 并发返回同一订单。
- 测试创建的临时数据库已全部删除；测试前后业务库记录数一致。
- 原有场次 5、6 的日期保留，没有移动历史场次。
- 业务库实际演示：订单 1 锁住两个座位后取消；订单 2 由五秒周期后台任务自动变为 EXPIRED。
- 演示结束后座位恢复 AVAILABLE，订单及状态历史保留。
- reservation-worker 保持运行；可用 docker compose stop reservation-worker 停止。
- 本轮未提交或推送 Git。

日志：[stage3-tests.txt](stage3-tests.txt)。实际订单、历史和库存快照：[stage3-demo.json](stage3-demo.json)。

复现测试：`docker compose run --rm --build tests`。
