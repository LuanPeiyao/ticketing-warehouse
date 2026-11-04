# 第七阶段：自动化测试与部署验收

验证日期：2026-11-04。API、前端与三语切换已通过 PR #3 合入 main（eb907d9）。

## 本机全新环境

macOS 15.7.9 / Apple Silicon ARM64，Docker Compose v2.32.4-desktop.1。
执行 `python3 scripts/verify_deployment.py --report-dir /private/tmp/ticketing-stage7-local`，约一分钟通过。

- 使用随机密码、随机本机端口、独立镜像标签和全新数据卷，不读取原开发库 .env。
- 五次迁移登记，重复初始化通过；演示场次重复生成复用原编号。
- 57 项真实 PostgreSQL Python 集成测试通过，含登录权限、订单、锁竞争、支付退款及升级兼容。
- 两个早期 SQL 文件共 14 项约束检查通过，末尾回滚。
- 宿主机 HTTP 检查首页、语言资源、就绪状态及 OpenAPI 成功；模拟失败支付、成功支付、退款、重售、旧退款重试及最终库存回收成功。
- 一秒占用订单由实际后台 worker 改为 EXPIRED，库存恢复 AVAILABLE。
- 测试库清理及受限应用角色检查通过；临时 Compose 容器和数据卷清理通过，镜像标签已移除。
- 原 ticketing-warehouse 的 PostgreSQL、API 与 worker 继续运行，未重建或清空。
- Node.js v22.14.0：语法检查通过，15 项前端与三语测试全部通过。

本机记录：[结果摘要](stage7-local-summary.json)、[Python 测试](stage7-local-tests.txt)。
GitHub Linux CI 的运行结果以 PR Checks / Actions 页实际状态为准。

本次自动化不覆盖新的浏览器交互测试，实际网页交互证据沿用 stage6 与 web-i18n 记录。
