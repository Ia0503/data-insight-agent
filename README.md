# Data Insight Agent｜数据洞察与智能分析平台

基于 Vue 3、FastAPI 和 LangGraph 的业务分析应用。结合 CSV 指标计算与文档检索，生成包含图表和原文引用的分析报告。

## 功能

- **数据工作区**：管理项目，导入 CSV 与文本型 PDF，检查数据质量、分页预览和下载原文件。
- **业务指标**：配置订单字段映射，计算实付销售额、净销售额、已支付订单数和退款订单比例，支持日期、地区、产品筛选及分组对比。
- **知识检索**：使用本地 BGE 模型与 pgvector 建立索引，按来源和相似度检索，定位 PDF 页码与 CSV 原记录。
- **智能分析**：通过受控工具计算指标、检索证据并生成结构化报告，提供实时进度、任务取消和模型用量记录。
- **报告与历史**：展示指标、趋势图、分组图和引用，支持历史筛选、问题复用、草稿恢复及手机端浏览。
- **调用保护**：每次新建分析输入调用密码，全站按中国标准时间自然小时限制模型用量；任务和额度持久保存，中断后不会自动重发。
- **示例与评测**：内置虚构业务示例，提供综合验收资料、固定指标与检索评测，以及直接调用 LLM 的对照测试。

## 技术栈

| 部分       | 技术                                                |
| ---------- | --------------------------------------------------- |
| 前端       | Vue 3、TypeScript、Vite、Pinia、Vue Router、ECharts |
| 后端       | Python 3.12、FastAPI、SQLAlchemy、Alembic、Pandas   |
| 分析与检索 | LangGraph、BGE-base-zh-v1.5、PostgreSQL、pgvector   |
| 测试       | pytest、Playwright                                  |

## 代码结构

```text
frontend/
  src/views/        # 工作区、分析、任务与历史页面
  src/components/   # 表单、原文预览、报告与图表
  src/composables/  # 实时任务状态
  src/stores/       # 项目状态
backend/
  app/api/          # 项目、分析与任务接口
  app/agent/        # 分析流程、模型适配与报告校验
  app/services/     # 文件处理、指标、索引与任务队列
  app/ingestion/    # CSV/PDF 解析
  app/models/       # 数据库模型
  app/schemas/      # 输入与输出契约
  migrations/       # 数据库迁移
test/               # 测试、示例数据与评测
deploy/             # 前后端镜像、Nginx 与备份工具
compose.yaml        # 前端、后端与 PostgreSQL / pgvector
.env.example        # 本地配置模板
```

## 容器运行

需要已启动的 Docker 引擎及 Docker Compose v2.24.4+。首次配置与模型下载方法见 [部署说明](deploy/README.md)。已有本地环境可以直接设置调用密码，然后启动：

```powershell
.\.venv\Scripts\python.exe backend/setup_access.py
docker compose up -d --build --wait
```

页面：[localhost:8080](http://localhost:8080)。首次构建会下载依赖；BGE 约 410 MB，保存在 `runtime/models/`，上传文件保存在 `runtime/uploads/`。数据库使用独立卷，停止容器保留数据。已有本机后端应先停止，应用只允许一个后端实例连接同一数据库。

在 `.env` 填写模型平台、地址、模型名称与密钥，设置 `LLM_CALLS_ENABLED=true`。配置修改后执行 `docker compose up -d --force-recreate --wait backend frontend`。默认每小时上限为 1,000,000 输入与输出 Tokens；服务商未返回可靠用量时按保守预留计入。

## 本机开发

需要 Python 3.12、Node.js 24、npm 和已启动的 Docker 引擎。在切换到本机开发前，先停止容器前后端：`docker compose stop frontend backend`。

在项目根目录使用 PowerShell，首次初始化：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -e ./backend
.\.venv\Scripts\python.exe backend/setup_local.py
.\.venv\Scripts\python.exe backend/setup_access.py
docker compose up -d --wait db
.\.venv\Scripts\python.exe -m alembic -c backend/alembic.ini upgrade head
.\.venv\Scripts\python.exe backend/setup_embeddings.py
npm --prefix frontend ci
```

初始化脚本生成本地 `.env`，不覆盖已有配置。BGE 首次下载约 410 MB，后续从 `runtime/models/` 加载。

启动数据库和后端：

```powershell
docker compose up -d --wait db
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

另开终端启动前端：

```powershell
npm --prefix frontend run dev
```

页面：[localhost:5173](http://localhost:5173) · 接口文档：[localhost:8000/docs](http://localhost:8000/docs)

启用智能分析时，在 `.env` 选择 `LLM_PROVIDER`，填写对应的 `BASE_URL`、`MODEL` 和 `API_KEY`，设置 `LLM_CALLS_ENABLED=true` 后重启后端。模板默认关闭真实调用；模型接入使用兼容 Chat Completions 的接口，需支持工具调用。当前已验证百炼接入，其他平台模板需自行验证。

## 使用与验证

导入订单 CSV → 保存字段映射 → 计算指标；为 PDF 或反馈 CSV 建立索引 → 检索并查看原文；选择问题和数据源 → 提交智能分析 → 查看报告与历史。

可在项目列表点击“导入业务示例”，或执行 `test/seed_comprehensive.py` 准备包含 2,440 条订单、120 条反馈和三份 PDF 的综合用例。所有数据均为虚构。

前端检查使用 `npm --prefix frontend run build`，测试与评测运行方法见 [test/README.md](test/README.md)。

## 当前边界

CSV 支持 UTF-8，PDF 需可提取文本；默认单文件上限 20 MB，不支持 OCR。退款指标按订单支付日期归属，汇总表不能与订单明细重复相加。

支持本地单实例与三服务容器运行，默认只允许本机访问。调用密码仅保护新增模型分析；工作区资料和历史报告没有账户权限控制，公网开放前需要配置 HTTPS、访问范围及服务器。智能分析可能出现模型格式、证据覆盖或超时失败，推断与建议需核对原文。API 密钥、上传文件和数据库数据保存在本地；真实分析可能向模型服务商发送选中资料。

## 许可证

本项目采用 [MIT License](LICENSE)。第三方依赖遵循各自许可证。
