# Warehouse — 离线数仓分层（ODS / DWD）

在业务库之上用独立 schema 建立的数仓分层，为后续 DWS/ADS 指标层与调度编排提供基座。
当前阶段用 PostgreSQL schema 分层实现；同一套分层逻辑之后可平移到 Spark/Hive 等引擎。

## 分层设计

```
public（业务库，只读来源）
   │  全量快照抽取（批次登记，只追加）
   ▼
ods.*     贴源层：12 张业务表原样快照 + _batch_id / _extracted_at
          认证表（auth_credential / auth_session）含敏感信息，不抽取
   │  清洗 / 加工 / 建模（幂等全量重建）
   ▼
dwd.*     明细层：
          维度  dim_user / dim_venue / dim_activity / dim_session
          事实  fact_order（一单一行，支付/退款/历史聚合为度量列）
                fact_payment / fact_refund（过程事实，保留每次尝试）
                fact_ticket（一票一行，带 seat_code / row_label）
   ▼  （下一步）
dws.*     汇总层：按用户/活动/日期粒度的 GMV、售出率、退款率等
ads.*     应用层：报表与看板指标
```

关键口径约定：

- **时区**：库里存 UTC，所有"本地日期/小时"属性在 `dim_session` 一次性派生
  （`AT TIME ZONE 'Asia/Shanghai'`），上层不再自行换算；
- **订单终态原因**：`fact_order.final_reason` 取流转到当前状态的那条历史记录；
- **支付耗时** `hold_minutes` = 支付成功时刻 − 下单时刻；
- **退款时长** `refund_delay_minutes` = 退款成功时刻 − 支付成功时刻。

## 使用

需要管理员账号（创建 schema 的权限），业务账号 `ticketing_app` 无此权限：

```bash
# 全流程：抽取批次 -> 装载 DWD -> 8 项对账（任何一项不平整体回滚）
python warehouse/scripts/run_warehouse.py

# 增加批次备注
python warehouse/scripts/run_warehouse.py --note "周报数据"

# 只对账（不抽取、不重建）
python warehouse/scripts/run_warehouse.py --verify-only
```

对账项：订单行数/金额、明细数、成功支付金额闭环（= PAID+REFUNDED 订单金额）、
票行数、paid_at 完整性、维度无孤儿事实、票事实可关联座位。

## 文件清单

| 文件 | 说明 |
|---|---|
| `sql/01_ods_ddl.sql` | ODS schema、批次登记表、12 张贴源快照表 |
| `sql/02_dwd_ddl.sql` | DWD schema、4 张维度表、4 张事实表及索引 |
| `sql/03_dwd_load.sql` | ODS → DWD 幂等装载（TRUNCATE + INSERT，消费最新 DONE 批次） |
| `scripts/run_warehouse.py` | 编排：抽取 → 装载 → 对账，失败整体回滚 |

## 与业务侧的边界

- 不修改业务表结构、不新增业务迁移、不影响任何现有接口；
- ODS 只追加（保留每批快照，可回溯），DWD 每次重建保证与最新批次一致；
- 本目录新增内容均为独立文件，不触碰 `ticketing/` 业务代码。
