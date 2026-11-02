# 第四阶段验证记录

验证日期：2026-11-02。平台：macOS / Apple Silicon，PostgreSQL 17.11、Python 3.12.16、Psycopg 3.2.13。

## 自动化验证

- Docker Compose：40 项 Python/PostgreSQL 集成测试通过，耗时 10.899 秒。
- 项目专用 Conda 环境 `ticketing-warehouse`：同样 40 项测试通过，耗时 11.200 秒。
- 升级后的开发库执行两份既有 SQL 脚本，14 个约束场景通过，样例写入已随事务回滚。
- 包含从空库建立全部四个版本、001/002 旧表接管，以及保留 003 待支付订单再升级到 004 的验证。
- 支付/退款的相同请求并发、支付与取消、过期清理与支付、退款与重新购票使用真实数据库连接。
- 日志包含数据库后端编号、Lock/transactionid 等待以及阻塞者编号。
- 支付或退款事务末尾注入故障后，尝试记录、订单、票和库存均完整回滚。
- 最终确认没有遗留 `ticketing_test_*` 数据库。

日志：[Docker 集成测试](stage4-tests.txt)、[Conda 集成测试](stage4-conda-tests.txt)、[SQL 回归](stage4-sql-tests.txt)。

## 本机开发库实际演示

004 已应用，现有十二张业务表及一个迁移登记表。
原订单 1 的 CANCELLED 和订单 2 的 EXPIRED 状态及金额保留。

| 步骤 | 结果 |
|---|---|
| 用户 11 对场次 13 的库存 19、20 下单 | 订单 3，金额 220.00，库存 LOCKED |
| 第一次模拟支付失败 | 订单仍 PENDING，不出票，失败尝试保留 |
| 新请求模拟支付成功并重试相同请求 | 订单 PAID，共两张 VALID 票，只生成一次成功支付 |
| 模拟退款失败 | 订单仍 PAID，两张票仍有效，库存仍 SOLD |
| 新请求整单全额退款成功 | 订单 3 为 REFUNDED，旧票失效，库存 AVAILABLE |
| 用户 12 再次购买相同库存并支付 | 订单 4，成功生成两张新票 |
| 重放订单 3 原退款请求 | 返回原退款，订单 4 的库存仍为 SOLD 且归订单 4 |
| 订单 4 全额退款结束演示 | 订单 4 为 REFUNDED，两张库存最终恢复 AVAILABLE |

实际订单、支付、票、退款、状态历史和最终库存见 [演示 JSON](stage4-demo.json)。
PostgreSQL 健康运行，`reservation-worker` 已重建并用新版本继续每五秒清理过期订单。

## 复现

```bash
docker compose run --rm --build tests
```

实际演示步骤见 [模拟支付与退款](../database/04_payment_refund.md)。编号会随数据库而变化，应先运行 `catalog`。
当前功能为 CLI + 本地同步支付模拟；未接入资金平台、HTTP API 或检票核销。
