# 第五阶段验证记录

验证日期：2026-11-02。平台：macOS / Apple Silicon，PostgreSQL 17.11，Python 3.12.16。
FastAPI 0.162.0、Uvicorn 0.54.0、Pydantic 2.14.2、Psycopg 3.2.13；固定依赖见 requirements.txt。

## 自动化验证

- Docker：55 项真实 PostgreSQL 集成测试全部通过，耗时 15.631 秒。
- Conda `ticketing-warehouse` 环境：同样 55 项测试全部通过，耗时 17.017 秒。
- 包含原有 40 项交易测试，以及新增 15 项 HTTP、认证、权限和响应契约测试。
- 注册拒绝角色/用户编号注入及覆盖已有用户；密码以 Argon2id 哈希存储。
- 登录失败计数持久化；连续五次失败锁定一分钟，过期后可重新登录。
- 令牌摘要存储、退出撤销、过期失效与用户停用校验通过。
- 他人订单查询、取消、支付和退款均返回 404，订单列表只包含当前用户记录。
- 参数错误不回显密码或请求原文；数据库异常只返回通用 503；OpenAPI 错误模型与实际响应一致。
- TestClient 并发请求竞争同一座位，只有一个订单成功；既有双连接行锁验证继续通过。
- 凭据写入故障后用户资料插入回滚；迁移可重复执行，旧版本迁移保留。
- Conda pip check 通过，没有依赖冲突。

日志：[Docker 测试](stage5-tests.txt)、[Conda 测试](stage5-conda-tests.txt)。

## 实际部署与 HTTP 验证

005 认证迁移已应用。本机存在十四张业务与认证表，以及 schema_migration，共十五张表。
API 健康检查通过，监听 `127.0.0.1:8000`；PostgreSQL 继续监听 `127.0.0.1:15432`。
后台过期清理任务运行，原订单 1～4 的状态保留。

通过真实网络端口完成两轮 HTTP 演示：

| 运行方式 | 新建订单 | 验证内容 | 最终状态 |
|---|---|---|---|
| 项目 Conda Python 调用宿主机 API | 5、6 | 注册登录、下单、失败支付、成功出票、幂等、退款、重售、旧退款重放、退出 | 两单均 REFUNDED，座位 AVAILABLE |
| Docker app 容器调用 api:8000 | 7、8 | 同一脚本完整运行，验证无需宿主机 Python 的部署路径 | 两单均 REFUNDED，座位 AVAILABLE |

四个演示账号的会话均已撤销；最终有效未撤销会话数为 0。
最终没有遗留 `ticketing_test_*` 数据库。
演示记录保留订单和票务历史，不包含密码或登录令牌。

详细结果：[HTTP 演示 JSON](stage5-demo.json)、[Docker 演示输出](stage5-docker-demo.txt)。
Swagger UI 已在浏览器实际打开，可看到 Authorize、登录、活动库存、订单和模拟支付退款接口。

## 复现

```bash
docker compose run --rm --build tests
docker compose run --rm --entrypoint python app scripts/api_demo.py --base-url http://api:8000
```

完整启动步骤、Swagger 操作和限制见 [FastAPI 使用说明](../api/05_fastapi.md)。
本阶段为本机教学 API，支付退款仍是同步模拟；未实现邮箱验证、密码找回、前端页面、真实支付和检票。
