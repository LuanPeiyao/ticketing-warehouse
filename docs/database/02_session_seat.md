# 第二阶段：场次座位库存 session_seat

> 本章记录 002 阶段；003 已加入 LOCKED、锁座订单与截止时间，最新实现见 [第三阶段](03_booking_transactions.md)。

迁移文件：`migrations/002_session_seat.sql`，在 001 之后执行一次。

## 一个物理座位，多份独立库存

物理座位 A1 可以用于两个不同的场次。每个场次分别配置价格和库存：

| 场次 | 物理座位 | 票价 | 当前状态 |
|---|---|---:|---|
| 2026-10-30 晚场 | A1 | 100.00 | AVAILABLE |
| 2026-10-30 晚场 | A2 | 120.00 | AVAILABLE |
| 2026-10-31 晚场 | A1 | 100.00 | AVAILABLE |
| 2026-10-31 晚场 | A2 | 120.00 | AVAILABLE |

`seat` 只有两行位置数据，`session_seat` 有四行库存。修改第一场 A1 的价格不影响第二场 A1。

## 字段

| 字段 | 类型 | 含义 |
|---|---|---|
| `session_seat_id` | `BIGINT` | 自动生成的库存主键 |
| `session_id` | `BIGINT` | 属于哪个场次 |
| `seat_id` | `BIGINT` | 对应哪个物理座位 |
| `venue_id` | `BIGINT` | 场次和座位共同所属的场馆，用于组合外键校验 |
| `price` | `NUMERIC(12,2)` | 该场次、该座位当前票价，必须是非负有限金额 |
| `status` | `VARCHAR(20)` | 本阶段仅允许 `AVAILABLE` |
| `created_at` | `TIMESTAMPTZ` | 创建时间 |
| `updated_at` | `TIMESTAMPTZ` | 触发器维护的修改时间 |

## 三项核心规则

### 同场次、同座位不能重复

```sql
UNIQUE (session_id, seat_id)
```

它防止重复创建库存记录。它不等于已经实现并发抢座或交易防超卖。

### 座位必须属于场次所在场馆

只分别引用一个存在的场次和一个存在的座位，不足以保证二者在同一场馆。
本迁移在两张父表增加组合唯一键，并使用两组组合外键：

```sql
FOREIGN KEY (session_id, venue_id)
    REFERENCES activity_session (session_id, venue_id)

FOREIGN KEY (seat_id, venue_id)
    REFERENCES seat (seat_id, venue_id)
```

同一个 venue_id 必须同时与场次和座位匹配，因此不能把另一个场馆的座位加入当前场次。
这里保留 venue_id 是为了声明并强制该关联规则。

### 订单阶段再引入锁座

当前只配置库存，数据库也仅接受 AVAILABLE 状态。
下一阶段建立订单表后，通过新迁移加入 locked_order_id、locked_until、LOCKED/SOLD 状态及其一致性约束。
本阶段尚未实现锁座、支付、取消和超时释放。

## 首次执行

本地协助开发的数据库已执行 002。新部署的空库先执行 001，再执行以下命令一次。
macOS / Linux 项目根目录：

```bash
docker compose exec -T postgres psql -X -U ticketing_admin -d ticketing -v ON_ERROR_STOP=1 < migrations/002_session_seat.sql
```

Windows 可先复制文件到容器，再用 psql 执行：

```bash
docker compose cp migrations/002_session_seat.sql postgres:/tmp/002_session_seat.sql
docker compose exec -T postgres psql -X -U ticketing_admin -d ticketing -v ON_ERROR_STOP=1 -f /tmp/002_session_seat.sql
```

后续变更新增迁移，不重新运行 001 或 002，也不通过删除原表更新结构。

## 可重复加载的样例数据

样例脚本会复用既有的校园大礼堂、A1/A2 和主办方，补齐校园音乐会、两个场次和四条库存。
活动保留 DRAFT、场次保留 SCHEDULED，本阶段只配置库存，不代表活动已发布或场次已开售。
邮箱、场次、库存通过唯一规则去重；场馆和活动名称出现歧义时会报错。
重复执行不重复添加业务记录，也不覆盖已有价格或售卖状态；Identity 序列可能前进。
样例使用固定演示日期 2026-10-30 / 2026-10-31，后续运行购票演示前应检查场次是否仍在未来。

```bash
docker compose exec -T postgres psql -X -U ticketing_admin -d ticketing -v ON_ERROR_STOP=1 < seeds/001_ticketing_demo.sql
```

脚本是可上传 GitHub 的可复现样例，数据库实际文件仍保存在本地数据卷中。

## 查看当前库存

进入 psql 后执行：

```sql
SELECT
    a.title,
    s.session_id,
    s.start_time AT TIME ZONE 'Asia/Shanghai' AS start_beijing,
    v.name AS venue_name,
    p.seat_code,
    ss.price,
    ss.status
FROM session_seat ss
JOIN activity_session s ON s.session_id = ss.session_id
JOIN activity a ON a.activity_id = s.activity_id
JOIN venue v ON v.venue_id = ss.venue_id
JOIN seat p ON p.seat_id = ss.seat_id
ORDER BY s.start_time, ss.session_id, p.row_label, p.seat_number;
```

当前演示应返回四行。样例中的数据行可通过上述关联追溯到活动、场馆、场次和物理座位。

## 验证

```bash
docker compose exec -T postgres psql -X -U ticketing_admin -d ticketing -v ON_ERROR_STOP=1 < tests/sql/002_session_seat.sql
```

脚本输出 6 项 PASS，覆盖跨场次独立定价、两组场馆关联、重复库存、负票价与当前状态范围。
测试用邮箱 `session-seat-check@example.test` 应未被业务记录占用，样例行在末尾回滚；已有演示数据保留。

技术依据：[PostgreSQL 17 组合外键与唯一约束](https://www.postgresql.org/docs/17/ddl-constraints.html)。
