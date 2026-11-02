# 第五阶段：FastAPI 接口、登录与订单权限

本阶段把既有 Python 交易服务提供为 HTTP 接口。FastAPI 接收并校验请求，然后调用原来的锁座、模拟支付和退款事务。
数据库继续使用 PostgreSQL，Docker Compose 增加 `api` 服务。

## 先启动并打开文档

首次克隆后，在仓库根目录执行（已有 `.env` 时保留）：

```bash
cp .env.example .env
docker compose up -d --wait --wait-timeout 120 postgres
docker compose run --rm --build db-admin
docker compose run --rm db-admin demo
docker compose --profile workers up -d --wait --wait-timeout 120 api reservation-worker
```

已有第四阶段环境升级：

```bash
docker compose stop reservation-worker
docker compose run --rm --build db-admin
docker compose --profile workers up -d --wait --wait-timeout 120 api reservation-worker
```

浏览器打开 `http://127.0.0.1:8000/docs`。接口定义在 `/openapi.json`，备用文档在 `/redoc`。
API 默认只绑定本机地址。端口占用时，在 `.env` 增加 `API_PORT=8001` 后重建 api 服务，并改用对应网址。
容器之间访问 `http://api:8000`，不使用宿主机映射端口。
Swagger UI 页面默认从 CDN 加载脚本，浏览器需要能访问该 CDN；HTTP 接口和 OpenAPI JSON 本身不依赖浏览器 CDN。

## 在 Swagger UI 中完成一次购票

1. 展开 `POST /auth/register`，点击 **Try it out**，填写邮箱、显示名称和 10～128 字符密码，再执行。
2. 使用 `POST /auth/login` 登录，复制结果中的 `access_token`。
3. 点击页面的 **Authorize**，粘贴令牌并授权；HTTPBearer 输入框只粘贴令牌，不加 `Bearer ` 前缀。
4. 查询 `GET /sessions`，选择未来场次；再查询 `GET /sessions/{session_id}/seats`，选择 AVAILABLE 库存。
5. 调用 `POST /orders`，填写场次、库存编号列表和新的 UUID。记录返回的订单编号与总额。
6. 调用 `POST /orders/{order_id}/payments`，金额使用返回的总额字符串，并提供新的 UUID；默认模拟成功。
7. 查询订单，可以看到 PAID、SOLD 对应的有效电子票。
8. 调用 `POST /orders/{order_id}/refunds`，填写原因和新的 UUID，完成开场前整单全额退款。
9. 查询订单及库存，确认旧票 REFUNDED、库存 AVAILABLE。最后调用 `POST /auth/logout` 退出。

生成 UUID：`python -c 'import uuid; print(uuid.uuid4())'`。Swagger 的 UUID 字段需要自行填入新的实际值。
同一操作重试复用原 UUID；支付或退款模拟失败后，要重新尝试成功需更换 UUID。

也可完全在 Docker 内运行可重复的实际 HTTP 演示，无需本机 Python：

```bash
docker compose run --rm --entrypoint python app scripts/api_demo.py --base-url http://api:8000
```

脚本为两个新演示用户生成随机密码，完成失败支付、成功出票、退款、重售和旧退款重放验证。
结束时退款释放本次座位并退出两个会话；保留演示用户、订单、电子票和交易历史，不输出密码或令牌。
异常时也尽量取消或退款本次创建的订单。没有两个可用座位时退出，不覆盖已有占用。
Conda 环境可直接执行 `python scripts/api_demo.py`；可加 `--output /path/to/demo.json` 保存不含登录凭据的验证记录。

## 接口清单

| 方法和路径 | 用途 | 是否登录 |
|---|---|---|
| GET /health/live | 进程存活 | 否 |
| GET /health/ready | 数据库连接及 005 迁移就绪 | 否 |
| POST /auth/register | 注册普通用户 | 否 |
| POST /auth/login | 登录获取令牌 | 否 |
| GET /auth/me | 查看当前用户 | 是 |
| POST /auth/logout | 撤销当前令牌，返回 204 | 是 |
| GET /activities | 有未来开售场次的已发布活动 | 否 |
| GET /sessions | 未来可售场次，可按 activity_id 筛选 | 否 |
| GET /sessions/{session_id}/seats | 场次库存、票价、状态 | 否 |
| POST /orders | 下单锁座，首次和幂等重试均返回 201 | 是 |
| GET /orders | 当前用户的订单列表 | 是 |
| GET /orders/{order_id} | 当前用户订单详情、票、支付退款尝试和历史 | 是 |
| POST /orders/{order_id}/cancel | 取消当前用户待支付订单 | 是 |
| POST /orders/{order_id}/payments | 本地模拟支付成功或失败 | 是 |
| POST /orders/{order_id}/refunds | 开场前整单全额模拟退款 | 是 |

列表使用 `limit` 和 `offset`，每页最多 100 条，默认活动/场次/订单 20 条，库存 100 条。
公共库存不返回占座者、订单归属或用户资料。
后台过期清理由独立 worker 执行，没有公开的 HTTP 清理接口。

## 请求与响应约定

下单体示例（编号以实际查询为准）：

```json
{
  "session_id": 13,
  "inventory_ids": [19, 20],
  "request_id": "9379db65-8ed5-4fbd-a0c8-d69fd7ef93c1"
}
```

这里没有 `user_id`：用户由 `Authorization: Bearer <access_token>` 决定。
注册只能创建 CUSTOMER，不接受指定角色、用户编号等额外字段。
下单、支付、退款要求 UUID 幂等编号；HTTP 端固定使用正常十分钟锁座，不开放 CLI 的短锁定演示参数。
金额输入、输出均使用十进制字符串，例如 `"220.00"`，避免客户端浮点误差；退款金额由原支付记录决定。

支付和退款默认 `outcome: "SUCCEEDED"`，改为 `"FAILED"` 可记录模拟失败。
HTTP 200 表示该操作已处理，业务是否成功还要查看 `payment.status` / `refund.status` 和订单状态。
超时后支付可能返回 HTTP 200、`payment: null` 和 EXPIRED，表示提交了过期释放；若 worker 已先清理则返回 409。

错误统一为以下结构，不回显请求原文、密码或数据库连接细节：

```json
{"error": {"code": "not_found", "message": "订单不存在"}}
```

| 状态码 | 含义 |
|---|---|
| 401 | 未登录、密码错误、令牌无效/过期/已撤销或用户停用 |
| 404 | 资源不存在；他人的订单也返回相同结果 |
| 409 | 库存、订单状态、支付金额、请求幂等冲突或重复邮箱 |
| 422 | 参数类型、格式、范围或额外字段不合法 |
| 429 | 连续五次登录失败后临时锁定，Retry-After: 60 |
| 503 | 数据库暂时不可用或迁移未就绪 |

## 登录如何工作

005 迁移新增 `auth_credential` 和 `auth_session`，共十四张业务与认证表，另有迁移登记表。
密码经 pwdlib 的 Argon2id 算法哈希保存，不保存明文。
登录产生 32 个随机字节的 URL 安全令牌，只在登录响应中返回一次；数据库保存其 SHA256 摘要。
令牌有效期一小时，每次请求核对有效期、撤销状态和用户状态；退出立即撤销当前令牌，其他设备会话保留。
连续五次密码错误会锁定该账号一分钟，失败计数在错误返回前提交；成功登录清零。

已有 CLI 用户只有资料，没有自动生成的密码。注册不能覆盖或接管这些邮箱；API 演示应注册新账号。
本阶段未实现邮箱验证、忘记密码、刷新令牌、后台用户管理和过期会话物理清理。
本机教学部署通过回环地址访问。若以后部署公网，需要 HTTPS、入口限流及相应运维配置。

## 代码分工与验证

| 文件 | 职责 |
|---|---|
| ticketing/api.py | 路由、认证依赖、资源归属、错误到 HTTP 的映射 |
| ticketing/schemas.py | 严格输入校验与明确的公开响应字段 |
| ticketing/auth.py | 注册、密码校验、会话、退出及登录失败计数 |
| ticketing/booking.py / payment.py | 继续负责原交易事务和并发保护 |
| migrations/005_api_auth.sql | 新表、外键、索引和数据形状约束 |
| tests/python/test_api.py | 真实数据库和 HTTP 契约集成测试 |
| scripts/api_demo.py | 实际网络端口上的完整演示 |

API 使用同步路由，让 FastAPI 在线程池执行同步 Psycopg 操作，避免在 async 路由里阻塞事件循环。
数据库账号新增所需的 INSERT/UPDATE 权限，仍无建表、删除交易记录或修改用户角色权限。
API 启动不自动执行管理员迁移，应先运行 db-admin；CLI 命令保持可用。

```bash
docker compose run --rm --build tests
```

Conda 开发先执行 `python -m pip install -r requirements.txt`，再按 [Conda 文档](../development/conda.md) 设置本机数据库连接变量。
随后可运行 `python -m uvicorn ticketing.api:app --host 127.0.0.1 --port 8001`，避免与容器映射的 8000 冲突。
当前不包含前端页面、真实支付、检票或数仓；下一阶段可在这些接口上扩展。
