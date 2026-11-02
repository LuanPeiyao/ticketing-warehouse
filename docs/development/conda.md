# Anaconda 本地开发环境

环境名称：`ticketing-warehouse`。本机位置：`/opt/anaconda3/envs/ticketing-warehouse`。
Python 3.12.16，与 Docker 的 Python 版本一致；项目依赖从 requirements.txt 安装。
Conda 管理本机 Python 环境，Docker 继续提供 PostgreSQL 和可复现的运行部署。

## 激活与验证

在 VS Code 新建终端，进入项目根目录：

```bash
conda activate ticketing-warehouse
python --version
which python
python -m pip check
python -m ticketing --help
```

激活成功后终端通常显示 `(ticketing-warehouse)`。
本机解释器应为 `/opt/anaconda3/envs/ticketing-warehouse/bin/python`。
退出环境使用 `conda deactivate`。

## VS Code

本机已通过 Python 环境选择器选中此解释器。
在新电脑上使用 Cmd+Shift+P（Windows/Linux 为 Ctrl+Shift+P），运行 Python: Select Interpreter。
选择 ticketing-warehouse，或手动输入当前机器上该环境的 Python 路径。
选择解释器后，新建终端使环境激活生效；已有终端可手动执行 conda activate。

## 在其他电脑复现

先安装 Anaconda 或 Miniconda，在项目根目录执行：

```bash
conda env create --file environment.yml
conda activate ticketing-warehouse
```

环境已存在时不必重新创建；依赖文件变化后执行：

```bash
python -m pip install -r requirements.txt
```

environment.yml 不包含本机绝对安装前缀或数据库密码，可提交 GitHub。
当前在 macOS ARM64 完成安装与运行验证，其他平台需要各自验证。

## 数据库连接的区别

项目当前默认连接参数面向 Docker 容器网络。本机 Conda 直接运行数据库命令时需要设置：

| 环境变量 | 本机开发值 |
|---|---|
| PGHOST | 127.0.0.1 |
| PGPORT | .env 中 POSTGRES_PORT，默认 15432 |
| PGDATABASE | .env 中 POSTGRES_DB，默认 ticketing |
| PGUSER | ticketing_app |
| PGPASSWORD | .env 中 TICKETING_APP_PASSWORD |

这些变量可在本机终端设置后再运行 `python -m ticketing catalog`。
数据库密码保留在本地 .env 或当前进程环境中，不写进 environment.yml。
本次已使用受限应用账号完成从 Conda 到 Docker 数据库的实际查询验证。

继续沿用 Docker 命令时，连接参数由 Compose 自动提供：

```bash
docker compose run --rm app catalog
docker compose run --rm tests
```

本机直接执行 tests/python 中的集成测试还需单独配置测试管理员凭据；日常验证优先使用上述 tests 服务，其测试库隔离流程已配置好。
