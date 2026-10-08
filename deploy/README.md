# 容器运行与维护

默认入口为 `http://localhost:8080`，前端 Nginx 代理内部后端，数据库仅绑定本机。应用当前为单实例；不要同时启动容器后端与连接同一数据库的本机后端。

## 首次启动

需要 Docker Compose v2.24.4+、Python 3.12。首次构建需要联网下载镜像和 CPU 依赖，模型缓存约 410 MB；后续无需重复下载。

Windows PowerShell，在仓库根目录执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install python-dotenv==1.2.4
.\.venv\Scripts\python.exe backend/setup_local.py
.\.venv\Scripts\python.exe backend/setup_access.py
docker compose build
docker compose run --rm --no-deps backend python backend/setup_embeddings.py
docker compose up -d --wait
```

已有 `.venv`、`.env` 和模型时复用即可。初始化不覆盖已有配置或密码，调用密码通过隐藏输入设置，本地只保存带随机盐的哈希。

Linux 对应使用 `python3.12` 和 `.venv/bin/python`。以普通用户运行初始化，脚本创建上传/模型目录，并在新配置中填入当前 `APP_UID`、`APP_GID`。已有配置迁移到 Linux 时，在 `.env` 填入 `id -u`、`id -g` 的值，确保该用户能读写 `runtime/uploads/` 和 `runtime/models/`、读取 `.env`。不要由 Docker 自动创建属于 root 的宿主机目录。

填写 `.env` 对应平台的 `BASE_URL`、`MODEL`、`API_KEY`，设置 `LLM_PROVIDER` 与 `LLM_CALLS_ENABLED=true` 后启用智能分析。其他功能不依赖生成模型。接口需兼容 Chat Completions 并支持工具调用；百炼已联调，其他平台需实际验证。

## 启动、更新与切换

```powershell
docker compose up -d --build --wait
docker compose ps
docker compose logs --tail 80 backend frontend
docker compose stop
```

修改 `.env` 后使用 `docker compose up -d --force-recreate --wait backend frontend`，保证重新挂载配置并重启后端。源代码更新后重新构建。启动时自动运行 Alembic 迁移，更新已有数据前应先备份。

`stop` 或 `down` 保留数据库卷及宿主机文件。不要执行 `down -v`，它会删除数据库卷。切回本机开发时停止容器前后端，按根目录 README 启动数据库、本机后端和 Vite；数据库与上传目录保持一致。

## 调用密码和额度

每次新建分析任务都要输入密码，一次任务内部的多次模型请求共用该次授权。密码不写入任务、草稿、报告或日志；确认已接收的原提交只读取原任务，不产生额外调用。缺少密码配置时拒绝新分析。

默认全站每个自然小时最多预留 1,000,000 输入与输出 Tokens，按 `Asia/Shanghai` 整点划分。跨小时请求归属于发送时的小时，记录保存于数据库。发送前原子预留保守上界，收到可信用量后核销；错误、取消、超时或缺少用量时保守计入预留。用量异常超过上界时记入实际数量并暂停该小时后续请求。任务没有自动网络重试，重启不清空额度，也不自动恢复中断任务。

密码错误达到 6 次时，该来源会暂时限制 15 分钟；入口另限制分析提交速率。默认后端没有宿主机端口，Nginx 覆盖客户端转发头，直接调用 API 也必须通过密码验证。

## 备份与恢复

在没有上传、索引或分析任务运行时，停止前后端并保存数据库、上传文件及配置：

```powershell
docker compose stop frontend backend
.\.venv\Scripts\python.exe deploy/backup.py
docker compose up -d --wait
```

备份脚本需要完整本机后端依赖：最小容器初始化环境可先执行 `python -m pip install -r backend/requirements.txt` 及 `python -m pip install --no-deps -e ./backend`。输出在忽略 Git 的 `runtime/backups/`，包含数据库 dump、上传压缩包、文件哈希清单和私密配置。保存到独立的安全位置；配置包含凭据，不要公开。模型可按固定版本重新下载，不在备份中重复保存。

恢复应先在独立的空数据库和上传目录中演练：核对 dump 和每个文件的 SHA-256，使用 `pg_restore --no-owner --exit-on-error` 导入空数据库，恢复上传目录及其相对路径，配置匹配的数据库凭据和文件权限，再启动并核对项目、指标、检索、报告及额度记录。步骤六的隔离恢复脚本见 `test/deployment/recovery.py`，它只接受测试数据库，拒绝覆盖已有目标表。

Git 标签只恢复代码。回退到旧迁移版本前，需要匹配的数据库、上传文件和配置快照；不要盲目降级或覆盖当前数据。实际数据恢复须先保留现状备份并确认覆盖范围。

## 外部访问

当前没有配置服务器、域名或公网入口。对外开放前需要服务器与持久存储、HTTPS、资料访问范围、备份位置和费用监控。`WEB_BIND=127.0.0.1` 默认只允许本机访问；调用密码不会阻止别人查看或修改工作区资料，因此不能单独作为私密数据的公网保护。

容器默认使用非 root 用户、只读根文件系统、临时目录和轮转日志。API 密钥通过本地配置文件挂载，构建上下文不包含 `.env`、运行数据和个人记录。
