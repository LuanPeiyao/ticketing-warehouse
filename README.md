# Ticketing Warehouse

活动票务交易与运营离线数仓项目。

## 项目目标

完成数据库课程项目，并逐步扩展为数据开发与数据仓库作品。

## 计划实现的功能

- 活动、场馆、场次与座位管理
- 选座锁座与订单创建
- 模拟支付、取消与退款
- 电子票查看
- 并发防超卖与过期锁座释放
- 运营离线数仓与报表

第一版暂不包含检票与核销。

## 计划使用的技术

- PostgreSQL
- Python / FastAPI
- Docker Compose
- Spark / Hive Metastore
- Airflow

## 当前进度

已完成初步业务设计、Docker + PostgreSQL 开发环境部署与验证。业务表与 API 尚未实现。

## PostgreSQL 开发环境

当前 Compose 只部署数据库；本阶段不创建业务表。Spark、Hive Metastore 和 Airflow 后续按阶段接入。

### 环境要求

- Git。
- Docker Desktop 已启动，或 Linux 上 Docker Engine 与 Docker Compose v2 已可用。
- 本机端口 `15432` 可用；冲突时修改 `.env` 中的 `POSTGRES_PORT`。
- 首次启动需要网络下载 PostgreSQL 官方镜像。

镜像固定为 `postgres:17.11-bookworm`，支持 ARM64 与 AMD64。当前选用 PostgreSQL 17，对应的数据目录为 `/var/lib/postgresql/data`。

### 首次启动

在 VS Code 的“终端 → 新建终端”中，确认终端位于项目根目录。
第一次使用时复制配置；如果已经有 `.env`，保留原文件：

```bash
cp .env.example .env
```

可在 `.env` 中设置自己的本地数据库密码。如果已经存在 `.env`，直接使用原配置，不要再次复制覆盖。
`.env.example` 中的示例密码仅供本地体验；`.env` 不提交 Git。

```bash
docker compose config --quiet
docker compose up -d --wait --wait-timeout 120
docker compose ps
```

看到数据库为 `healthy` 后即可连接。数据库名、账号和密码只在数据卷第一次初始化时生效；后续修改 `.env` 不会自动修改已有数据库账号或密码。

### 连接数据库

无需在电脑上另外安装 PostgreSQL 或 `psql`，可以使用容器内的客户端：

```bash
docker compose exec postgres psql -U ticketing_admin -d ticketing
```

进入后运行：

```sql
SELECT current_database(), current_user, version();
SHOW timezone;
\dt
```

`\dt` 当前应提示没有关系，因为业务表尚未创建。输入 `\q` 退出客户端。
如果修改了 `.env` 中的账号或库名，连接命令也应相应调整。

从 VS Code 的数据库扩展或其他桌面客户端连接时使用：

| 参数 | 默认值 |
|---|---|
| Host | `127.0.0.1` |
| Port | `15432`，或 `.env` 中设置的端口 |
| Database | `ticketing` |
| User | `ticketing_admin` |
| Password | 本地 `.env` 中的 `POSTGRES_PASSWORD` |

`ticketing_admin` 是本地初始化管理员；接入 API 前再为应用创建独立的受限账号。
后续同一个 Compose 网络内的应用使用 `postgres:5432` 连接，电脑上的客户端使用 `127.0.0.1:15432`。
数据库默认时区为 UTC，后续业务展示按需要转换为 Asia/Shanghai。

### 日常操作与数据保存

```bash
docker compose logs --tail=50 postgres
docker compose stop
docker compose up -d --wait --wait-timeout 120
```

PostgreSQL 文件存放在命名数据卷 `ticketing-warehouse_pg_data` 中。
`docker compose down` 移除容器和网络，保留该数据卷，之后再次启动仍可使用原数据。
`docker compose down -v` 会删除数据卷，不用于日常停止服务。
数据卷用于持久化，备份需单独进行；数据库文件和本地密码不上传 GitHub。

### 常见问题

- 提示无法连接 Docker 引擎：先启动 Docker Desktop，等待引擎就绪。
- 下载镜像超时：检查 Docker Desktop 的网络或代理配置后重试，保留官方镜像来源。
- 提示端口占用：修改 `.env` 中的 `POSTGRES_PORT`，再次执行启动命令。
- 提示密码错误：已有数据卷中的密码不会随 `.env` 自动更新，使用初始化时的密码，或明确执行数据库密码变更。
- 展示和提交配置时使用 `.env.example`；`docker compose config` 的完整输出可能包含本地密码，校验配置优先使用 `--quiet`。

### 已验证的环境

2026-10-21，在 macOS / Apple Silicon ARM64 上完成验证：

- Docker Engine 27.5.1、Compose v2.32.4。
- PostgreSQL 17.11，数据库 `ticketing`，数据库时区 UTC。
- 本机 `127.0.0.1:15432` 可连接，容器内 TCP 密码认证查询通过。
- 删除并重建本项目容器后，数据库集群标识与临时测试注释仍保留，数据卷持久化通过。
- 测试注释已恢复，`public` 中业务表数量为 0。

AMD64 与其他操作系统尚未进行运行验证。
