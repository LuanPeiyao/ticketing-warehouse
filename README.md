# Ticketing Warehouse

活动票务交易与运营离线数仓项目。以数据库课程项目为起点，逐步扩展为数据开发与数据仓库作品。

## 当前进度

已完成 Docker + PostgreSQL、九张业务表、下单锁座、取消、超时释放与命令行演示。
提供迁移登记、可重复样例、受限应用数据库账号，以及真实 PostgreSQL 双连接并发验证。
支付、电子票、退款、FastAPI 与离线数仓在后续阶段实现。第一版不包含检票与核销。

## 本地快速部署

需要 Git、已运行的 Docker Desktop（或 Docker Engine + Compose v2）以及首次下载镜像的网络。
无需在本机安装 Python、PostgreSQL 或 psql。

```bash
git clone --branch codex/database-schema https://github.com/xiaoyeZh/ticketing-warehouse.git
cd ticketing-warehouse
cp .env.example .env
docker compose up -d --wait --wait-timeout 120 postgres
docker compose run --rm --build db-admin
docker compose run --rm db-admin demo
docker compose run --rm app catalog
```

私有仓库克隆需要 GitHub 身份验证。上述克隆命令要求先将本地开发分支发布到 GitHub。
当前实现位于 `codex/database-schema`，尚未合并 main；合并后可将克隆命令的分支改为 main。

已有 `.env` 时保留原文件，不再次复制覆盖。新增应用密码配置见 `.env.example`；本次本地开发已自动补齐。
`.env` 存放个人配置，不提交 Git、不进入 Docker 构建上下文。
示例密码供本地体验，正式共享实例应配置独立密码。

启动后数据库默认监听本机 `127.0.0.1:15432`，端口冲突时修改 `.env` 中的 POSTGRES_PORT。
数据库名默认 `ticketing`；本机数据库客户端使用初始化管理员和 `.env` 中的密码。
容器间使用 `postgres:5432`，数据库时区为 UTC，catalog 按 Asia/Shanghai 展示场次开始时间。

## 服务分工

| Compose 服务 | 作用 | 运行方式 |
|---|---|---|
| `postgres` | PostgreSQL 17.11 | 默认常驻，命名卷持久化 |
| `db-admin` | 迁移与演示数据初始化 | 按需运行，使用初始化管理员 |
| `app` | 下单、取消、查询、手动清理 CLI | 按需运行，使用 ticketing_app |
| `reservation-worker` | 每五秒处理过期订单 | workers profile，可常驻 |
| `tests` | 独立临时数据库中的集成测试 | 按需运行 |

Python 镜像固定为 3.12.16-slim-bookworm；Psycopg 3.2.13。
应用数据库账号没有建表或删除交易记录权限；当前 CLI 是本地教学入口，登录与 API 认证后续实现。

## 下单与取消

先运行 catalog 查看实际编号。下面中文占位符需要替换为输出中的数字：

```bash
docker compose run --rm app reserve --user 用户编号 --session 场次编号 --seats 库存编号A 库存编号B
docker compose run --rm app order --user 用户编号 --order 订单编号
docker compose run --rm app cancel --user 用户编号 --order 订单编号
```

座位参数使用 session_seat_id，而非物理 seat_id。
默认占用十分钟；只有已发布、开售、尚未开始且场馆和座位启用的场次才能下单。
同一订单的座位必须全部可售，否则完整回滚。
相同用户重试同一操作使用原 `--request-id` UUID；更换场次或座位集合需要新请求编号。

快速过期演示可在 reserve 后加 `--hold-seconds 2`，超过截止时间后：

```bash
docker compose run --rm app expire
```

启用或停止自动清理：

```bash
docker compose --profile workers up -d --build reservation-worker
docker compose logs --tail=20 reservation-worker
docker compose stop reservation-worker
```

## 迁移和样例

`db-admin` 按顺序执行 migrations，并在 schema_migration 保存校验值。
重复运行跳过已执行版本；不能通过修改旧迁移来更新已有库，应新增迁移。
早期已经手工执行 001/002 的本地库会先核对已知结构，再登记旧版本并应用 003；不重建旧表。

| 迁移 | 内容 |
|---|---|
| 001 | 用户、场馆、物理座位、活动、场次 |
| 002 | 场次库存与独立定价 |
| 003 | 订单、明细、状态历史、库存锁座关联 |

`db-admin demo` 保留历史场次，创建或复用未来演示场次和两个普通用户。
重复运行不重置已有价格或占用状态；没有未来演示场次时创建新的未来场次。
`seeds/001_ticketing_demo.sql` 是早期固定日期样例，保留用于学习，当前下单演示使用动态未来样例。

## 测试

```bash
docker compose run --rm --build tests
```

测试自动创建独立的 ticketing_test_* 数据库，使用真实数据库连接验证事务、权限、并发和幂等；结束后只删除该测试库。
并发证据包含数据库后端编号、Lock 等待和阻塞者编号。
验证日志和本地演示结果保存在 [docs/verification](docs/verification/)。

早期 SQL 约束脚本也可通过管理员 psql 在开发环境执行，样例在末尾回滚：

```bash
docker compose exec -T postgres psql -X -U ticketing_admin -d ticketing -v ON_ERROR_STOP=1 < tests/sql/001_core_tables.sql
docker compose exec -T postgres psql -X -U ticketing_admin -d ticketing -v ON_ERROR_STOP=1 < tests/sql/002_session_seat.sql
```

上述输入重定向适用于 macOS/Linux。Windows 可用 docker compose cp 将 SQL 复制到容器，再用 psql -f 执行。
如自定义了库名或管理员名，替换 psql 命令中的参数。

## 数据保存与常用操作

```bash
docker compose ps
docker compose logs --tail=50 postgres
docker compose exec postgres psql -U ticketing_admin -d ticketing
```

psql 中用 `\dt` 列表，`\d booking_order` 查看结构，`\q` 退出。
现在有九张业务表及 schema_migration 技术表。
数据库文件保存在 ticketing-warehouse_pg_data 命名卷中；重建容器保留数据，备份仍需单独进行。
日常停止使用 docker compose stop；docker compose down 保留命名卷，down -v 则会删除数据卷。
POSTGRES_* 初始化账号和库名只在首次创建数据卷时生效，之后修改 .env 不会自动修改已有管理员密码。

## 学习文档

- [五张基础表](docs/database/01_core_tables.md)
- [场次库存与组合外键](docs/database/02_session_seat.md)
- [订单事务、双连接并发与部署说明](docs/database/03_booking_transactions.md)

已在 macOS / Apple Silicon ARM64 验证。AMD64 与其他宿主系统尚未进行运行验证。
首次拉取官方镜像遇到连接中断时，可检查 Docker Desktop 网络或代理后重试构建。

## Anaconda 本地开发

项目提供 [environment.yml](environment.yml)，创建独立的 ticketing-warehouse 环境，使用 Python 3.12.16 和固定项目依赖。
本机环境已创建；激活使用 `conda activate ticketing-warehouse`。
其他电脑在仓库根目录执行 `conda env create --file environment.yml`。
VS Code 解释器选择与本机数据库连接参数见 [Conda 开发说明](docs/development/conda.md)。
