# 第七阶段：自动化测试与全新部署验收

交易系统、FastAPI、轻量前端和三语切换已合入 `main`。本阶段让后续改动可以自动验证，并提供可复现的部署验收入口。

## GitHub Actions 怎么工作

工作流位于 `.github/workflows/ci.yml`。推送到 `main` 或 `codex/**`、向 `main` 提交 PR，都会触发 CI；也可在 Actions 页手动运行。

| 检查 | 验证内容 |
|---|---|
| Frontend checks | JavaScript 语法、金额精度、请求编号、文本转义与三语词条、语言偏好等前端测试 |
| PostgreSQL and fresh deployment | 构建应用镜像、全新 PostgreSQL、迁移与样例重复执行、Python 集成测试、SQL 约束、真实 HTTP 交易、后台过期清理 |

两个任务在 GitHub 提供的 Ubuntu 24.04 环境分别运行。测试使用随机生成的一次性密码，不需要在 GitHub 配置数据库密码或连接本机数据库。
工作流只授予仓库读取权限，第三方 Actions 固定到具体提交。它验证代码，不发布服务器或自动上线。

在仓库 Actions 页打开运行记录，两个任务均为绿色才表示本次 CI 通过。PR 的 Checks 页会显示同样的结果。
失败时展开红色步骤查看原因；部署验收的日志和结果位于该次运行的 `deployment-*` artifact 中，保留七天。取消或超时可能中断日志上传。
当前没有启用“必须通过检查才允许合并”的分支保护，合并前仍需主动确认检查结果。

## 在自己电脑重现全新部署

需要运行中的 Docker、Compose v2 和 Python 3.10+。脚本仅使用 Python 标准库，不要求激活 Conda；正常部署本身仍只需要 Docker。

在仓库根目录运行：

```bash
python3 scripts/verify_deployment.py
```

Windows 如果没有 `python3` 命令，使用 `python scripts/verify_deployment.py`。结果默认写入 `artifacts/deployment/`，此目录不提交 Git，也不会进入镜像。
可以用 `--report-dir` 指定单独的报告目录。

脚本会复制 Dockerfile 需要的当前源码，不复制个人 `.env`、原数据库或 `.git`。随后创建随机名称的 Compose 项目、独立镜像标签、新数据卷及随机本机端口，并执行：

1. 构建镜像并启动全新 PostgreSQL，应用迁移，再次运行初始化以检查可重复性。
2. 两次生成未来演示场次，确认复用相同场次编号。
3. 运行真实 PostgreSQL Python 集成测试和早期 SQL 约束脚本，检查临时测试库已删除、应用角色无超级用户或建库权限。
4. 启动 API 与后台清理进程，通过宿主机 HTTP 检查首页、语言资源、就绪探针和 OpenAPI。
5. 注册两个临时用户，执行模拟失败支付、成功支付、幂等重试、退款、重售、旧退款重试及再次退款；确认库存恢复可售。
6. 创建一秒后过期的订单，等待实际 worker 将其关闭并释放座位。
7. 保存日志，删除本次临时项目的容器、网络和数据卷，移除本次镜像标签；确认没有遗留容器和数据卷。

脚本会在正常结束和可捕获异常时清理本次资源，不操作日常 `ticketing-warehouse` 项目。系统强制终止进程或 Docker 守护进程不可用时无法保证清理，报告中的 `project` 可用于识别本次资源，恢复 Docker 后再处理。
Docker 构建缓存和拉取的基础镜像保留，便于下次运行。全新部署指新配置与空数据卷，不表示每次清空本机 Docker 缓存。

## 如何看验收结果

- `summary.json`：总体状态、平台、迁移与样例验证、HTTP 交易、worker 和清理结果。
- `python-tests.txt`：完整 Python 测试记录，包含并发锁等待证据。
- `sql-*.txt`：早期数据库约束验证结果。
- `http-demo.json`：临时环境的订单、支付、退款与库存回执，不包含密码和令牌。
- `service-logs.txt` 和 `cleanup.txt`：服务日志与临时资源清理记录。

前端实际浏览器交互与布局证据仍见第六阶段及三语切换验证文档；Node 测试不替代浏览器测试。

## 日常开发顺序

从最新 `main` 创建功能分支，修改并在本地检查，提交推送后查看 CI，再创建 PR。通过检查、确认变更范围后合并，最后将本地 `main` 同步到远端。
数据库结构变化继续新增迁移，不改动已经登记的旧迁移。数仓功能在后续独立分支中开发。
