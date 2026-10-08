# 测试与验证

所有测试数据、测试代码、测试配置与结果集中在本目录。data/demo为步骤一小样本；data/business为明确标记的虚构订单、汇总、反馈和中文PDF，expected.json保存手算指标和文件/页码/记录目标。

## 环境

先按根 README 启动数据库并安装依赖。在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r test/requirements.txt
docker compose exec -T db psql -U newai -d postgres -c 'CREATE DATABASE newai_test;'
.\.venv\Scripts\python.exe test/generators/generate_fixtures.py
.\.venv\Scripts\python.exe test/generators/generate_business.py
```

测试只接受数据库名为 newai_test 的 TEST_DATABASE_URL；数据库已存在时无需重复创建。

开发网页手动联调例外：`node test/evaluation/dev_live_web.mjs --run test/results/NEW_UNIQUE_DIRECTORY` 在根目录执行，检查已有8000/5173服务与固定“业务分析示例(test)”。它实际提交一次计费任务并保留在开发库，要求先明确授权并启用调用；不启动服务、不替换模型、不自动重试，不进入自动回归。新目录必须位于test/results且尚不存在。普通Python/浏览器回归仍强制关闭调用并使用newai_test。

在开发服务已经启动后，可从项目根目录执行 `.\.venv\Scripts\python.exe test/seed_demo.py`，将两个小样本导入本机开发工作区。此操作明确写入开发库，用于人工检查界面；不会删除或覆盖已有项目。

## Python 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -c test/pytest.ini --junitxml=test/results/pytest.xml
.\.venv\Scripts\python.exe -m ruff check backend test --config test/config/ruff.toml
.\.venv\Scripts\python.exe -m ruff format --check backend test --config test/config/ruff.toml
```

测试自动迁移独立测试库，不清空开发库。测试会新增自己的项目和上传记录，测试产物在 results/，不提交 Git。

## 浏览器测试

```powershell
cd test
npm ci
npm run e2e
```

Windows 默认使用已安装的 Edge。测试自动启动 8001/5174 端口上的独立测试服务，结束后关闭。若无 Edge，可以安装测试专用 Chromium：

```powershell
npx playwright install chromium
$env:E2E_CHANNEL = 'chromium'
npm run e2e
```

Linux 使用项目 .venv/bin/python，并设置 E2E_CHANNEL=chromium。results/playwright.json 和失败 trace 保存实际运行结果。

手机交互回归覆盖 320、375、390、430、768px，使用 Edge 触摸模拟检查创建/编辑、长名称、多个文件、错误与重试、预览自动定位和返回、实际下载文件名、PDF 翻页、按钮点击高度、页面溢出与数据区域滚动。390px 检查正常平滑滚动，其余检查减少动画模式；320px 还检查服务异常提示。820/1440px 检查桌面表格及不需要滑动时不显示提示。截图集中于 results/，这些模拟结果不代表已验证手机软键盘与系统文件选择器。

## 健壮性与日志验证

integration/test_robustness.py 检查并发重试、无 Content-Length 分块上传及临时文件关闭、缺失原文件、PDF 提取异常和截断、应用日志关联及敏感内容保护。unit/test_parsers.py 补充空白 CSV、非有限数值和加密 PDF。e2e/robustness.spec.ts 检查详情组件复用竞态、取消编辑、预览重试、请求超时与重复提交、无效响应和连接状态更新。结果集中于 results/。

## 步骤二指标与真实检索

integration/test_analysis.py验证手算指标、字段映射、前导零、工具白名单、索引互斥、重建失败保留旧版本和跨项目引用隔离。test_index_boundaries.py验证重启、队列满及维度异常；unit/test_embeddings.py验证日期/精度、分块偏移、模型规则fingerprint和实际终止超时子进程。

审查回归增加首次映射并发保存、日期时区/聚合溢出、索引领取与收尾故障恢复、提交确认丢失时保护就绪索引、左右补齐的 last 池化和脱敏模型诊断；CSV 单元格前导零/NA/金额表示及 LF/CRLF 换行保真。e2e/audit.spec.ts 模拟接口覆盖 CSV 工具字段清理、手动/自动版本切换及晚到的检索/引用响应，模拟结果不用于语义质量评价。

浏览器新增映射/指标/通用工具、390px真实BGE索引/检索/PDF与反馈引用、失败及路由延迟保护；保留步骤一用例。先按根README下载模型。

在其他测试服务停止后，从项目根目录运行真实检索评测：

```powershell
.\.venv\Scripts\python.exe test/evaluation/run_retrieval.py
```

仅写newai_test，真实加载固定BGE，保存目标命中排名、耗时和原文切片核对至results/retrieval-evaluation.json。五个问题不代表泛化能力，模拟向量不计语义得分。pytest/评测/浏览器都有启动恢复，不要同时针对一个测试库运行。

seed_business.py明确写本地开发库，仅为可选人工界面检查，不属于自动测试。

## 步骤三 Agent 自动验证

unit/test_agent_model.py 使用 HTTPX MockTransport 验证本地配置、百炼/DeepSeek 参数区别、reasoning_content 续接、响应大小、鉴权/超时/重定向失败和数字/引用来源。

integration/test_agent.py 使用模拟生成模型结合真实 newai_test 数据库与 CSV 工具，覆盖手算指标、跨项目/选中数据隔离、幂等、无效工具参数、报告修复上限、调用预算、单次/总期限、取消、原文件/映射变化、固定历史索引、重启与数据库收尾确认丢失。

e2e/agent.spec.ts 覆盖禁用模型的真实页面入口，以及模拟报告展示、原文高亮/返回焦点、不确定提交的编号复用、取消后的晚到轮询、320px 布局与文本转义。模拟模型结果只证明流程和交互，不证明真实 LLM 能力。测试服务强制关闭 LLM_CALLS_ENABLED，即使本地填写密钥也不发起真实模型请求；不得将其打开用于自动回归。

真实生成模型联调不加入默认自动回归。完整 Python 与浏览器测试仍按前述命令顺序执行，不同时操作测试库。实际结果在 results/python-step3.xml、results/playwright.json 与截图/失败 trace 中。

手动检查本地 `.env` 中的百炼配置：

```powershell
.\.venv\Scripts\python.exe test/check_aliyun_llm.py
```

该命令会产生一次真实 API 调用，可能计费：关闭思考模式，只要求回复 OK，输出上限64 tokens，总期限60秒，无自动重试或重定向。只读取百炼三项配置，不访问数据库、不修改 `.env`、不启用 Agent。输出状态码、受控提示、用量和耗时，脱敏结果保存于 `results/aliyun-smoke.json`；不打印或保存密钥、原始响应、请求头及底层异常。退出码0表示收到有效文本，1表示失败或尚未确认；`expected_reply` 表示是否恰好回复 OK。连通性通过不代表工具调用或完整分析能力已验证。

`unit/test_llm_probe.py` 使用模拟响应检查成功、鉴权失败、重定向与网络失败，验证单次请求及输出不泄露密钥；默认回归不会执行上述手动命令。

真实 Agent 人工联调使用 `evaluation/run_agent_live.py --run`，只接受本地 `.env` 明确的 `newai_test`，发送虚构样本到百炼，单任务最多十六次/全轮默认最多二十八次模型请求，无失败任务自动重发。`--case normal|missing|injection` 可只测一项，`--max-calls` 可减少请求上限；未加 `--run` 只显示帮助。`evaluation/complete_agent_report.py --run` 仅补测已保存的指令干扰场景报告，最多两次计费请求，不改旧任务终态，不是生产恢复接口。不要与其他使用测试库的服务或评测并行。

```powershell
.\.venv\Scripts\python.exe test/evaluation/run_agent_live.py --run
node test/node_modules/@playwright/test/cli.js test --config=test/config/agent_live_ui.config.ts
```

后者读取已完成真实报告，后端仍禁用模型；核验1440/320px指标、原文高亮、返回焦点、重启读取、幂等及项目隔离，不发起新模型请求。结果集中 `results/agent-live/`。`unit/test_live_evaluation.py` 验证历史数据/未知判据及失败诊断，默认回归只用模拟模型。实际用量和失败诊断保存在测试结果中；小样本不代表模型的普遍可靠性。

## 数据库持久化检查

在没有进行文件导入时，从项目根目录执行：

```powershell
.\.venv\Scripts\python.exe test/verify_persistence.py
```

此检查会创建一条标记为“持久化验证项目(test)”的开发记录，并重启本项目数据库容器检查其是否保留，不删除数据卷。请不要在其他数据库任务运行时执行。

新增 unit/test_agent_facts.py 覆盖服务端事实/摘要、原文摘录与未知引用；integration/test_agent.py 覆盖中国标准时间、当前/基期退款统计和历史报告读取。报告补测现在可通过 --case normal|missing|injection 指定已保存的虚构场景，默认 injection，仍最多两次请求。

节点审查回归：integration/test_milestone.py验证CSV原始列名/筛选值保真、Agent通用工具、业务映射及CSV/PDF历史预览前后与下载哈希守卫；unit/test_milestone_contracts.py验证索引列名与最终结构失败诊断脱敏。浏览器覆盖历史CSV原文变更提示、下载哈希、焦点返回，以及检索PDF/CSV跨页继续携带历史哈希。所有自动回归仍不发起真实生成模型请求。

## 步骤四网页与接口验证

integration/test_step4.py 覆盖稳定游标分页、中国标准时间日期、字面关键词/英文大小写、无效输入、项目隔离、250 条 SSE 事件补收、Last-Event-ID、心跳/断开、数据库失败脱敏；内置示例并发创建、固定文件白名单、重复导入、映射保护、排队索引保护、原文件变化拒绝、部分失败重试和改名后识别。各例使用独立测试示例标记，避免持久化测试数据互相影响。

e2e/step4.spec.ts 覆盖项目草稿隔离、刷新后确认原编号、历史复用不提交、失效文件、历史筛选/分页/返回/相同查询刷新、取消与晚到刷新、图表异常/空值/负值/精度保留/分类回退/文本转义、320/390/768/1440px报告及原文返回。SSE 用临时本机 HTTP 分块服务核对浏览器真实 EventSource 重连与事件去重；拦截模拟不用于宣称实际传输已验证。

e2e/step4-real.spec.ts 通过真实网页/API 导入固定五个虚构文件，并使用实际本地 BGE 建三个索引；重复导入核对项目/文件ID、映射版本及 active 索引数，没有生成模型提交。首次 CPU 模型加载可能较慢，该例超时上限90秒。所有创建的常规测试/示例项目使用(test)后缀，框架的测试文件后缀与无效输入用例不改。

完整回归仍依次运行前述 pytest、npm run e2e。Python结果为results/python-step4.xml；浏览器汇总为results/playwright.json，步骤四截图为results/step4-*.png。已有真实报告的只读兼容检查继续使用agent_live_ui.config.ts；分别保持AGENT_LIVE_RESULT默认值与results-normal.json，仍不新增付费请求。

手动服务重启前后可在根目录执行`.\.venv\Scripts\python.exe test/verify_dev_state.py`：只读newai开发库，确认没有活动任务，输出项目名称/文件ID与哈希/索引状态/映射版本供比较；默认要求生成模型调用关闭。开发网页已授权开启时加`--allow-enabled`仅允许读取，不修改开关、请求模型或重启服务。这是状态核对，不能代替数据库及上传目录备份。

## 步骤四真实网页模型联调

仅在明确授权计费后，从项目根目录运行；先停止针对 newai_test 的其他测试服务。默认回归仍强制关闭模型，本脚本只为独立8002/5175服务启用调用，不修改.env、开发库或默认服务。

```powershell
$env:STEP4_LIVE_RUN = '1'
$env:STEP4_LIVE_DIR = Join-Path (Get-Location) ('test/results/step4-live-' + (Get-Date -Format yyyyMMdd-HHmmss))
node test/node_modules/@playwright/test/cli.js test --config=test/config/step4_live.config.ts
```

默认三个场景：正常分析、数据不足、资料指令干扰；最多32次模型请求，单任务仍最多16次模型/12次工具/一次共享修复，不自动重试测试或任务。只提交虚构数据。网页真实提交后验证SSE、活动任务刷新、终态报告/图表/原文定位、历史筛选/问题复用、幂等及用量；结束前只读核对手算、引用切片和服务端事实。

按需设置STEP4_LIVE_CASE为normal、missing或injection，只运行指定场景；STEP4_LIVE_MAX_CALLS可限定本次总请求数（1至48，默认32）。已有manifest的目录拒绝复用，保留失败任务与原始结果。产物只有正常API报告、虚构工具结果、受控事件/诊断及截图，不保存模型回复、思考或密钥；trace关闭。报告诊断不改变生产校验或修复预算。结束后清除这四个测试环境变量。

需要验证已保存的新报告而不计费时，设置AGENT_LIVE_DIR为该normal场景产物目录，AGENT_LIVE_RESULT为verified-normal.json，然后运行`node test/node_modules/@playwright/test/cli.js test --config=test/config/agent_live_ui.config.ts`；模型由默认测试后端强制关闭。原先两种报告的默认路径/命令保持兼容。目录和截图始终位于test/results内，不依赖执行命令的工作目录。

真实调用结果与失败诊断保存在 results/；小样本不能代表模型泛化准确率。

## 步骤五固定评测

data/evaluation/cases.json集中保存12项指标、12项检索、6项Agent，明确开发/保留集。原frozen.json记录首次冻结文件、配置和代码SHA；frozen-v2.json保留相同输入与模型，只固定修正CSV记录引用后的评分器版本。原清单及第一次失败/误拒产物保留，不覆盖。

在根目录顺序执行，不能与pytest、浏览器测试或其他newai_test服务同时运行：

```powershell
.\.venv\Scripts\python.exe test/evaluation/run_batch.py --offline --output test/results/step5-offline-new
# 下列命令真实计费，仅在明确授权后运行；本阶段累计上限64次，已用额度不会因换目录重置。
.\.venv\Scripts\python.exe test/evaluation/run_batch.py --live --run --output test/results/step5-live-new
```

输出目录必须不存在，路径限定test/results。CLI不自动覆盖或续跑；step5-budget.json记录阶段每次请求，失败仍占用；step5-active.lock防止同一评测并发，中断后先确认原进程和任务状态，不盲目删除锁或重提任务。应用继续使用原16次模型/12次工具/一次修复上限。普通自动测试强制关闭真实调用。

基线使用同平台/模型/问题/选中文件，一次调用直接阅读完整小样本原资料，没有工具或检索。评分分别检查目标指标、记录、基期、分组、文档覆盖及原文定位。自由事实、未知和因果由开发助手逐条阅读复核，仍需独立人工审阅；任务失败与not_run保留在总分母中。保留集本次已消费，后续不能将其标为未见验证集。

仅重新评分已有答案，不调用模型或访问数据库：

```powershell
.\.venv\Scripts\python.exe test/evaluation/rescore_batch.py --source test/results/step5-live-20261007/results.json --output test/results/step5-rescore-new.json
```

重评分先核对原输入SHA、全部用例及来源路径，保留前后判据和源结果哈希。CSV基线引用可来自准确记录的原文或解码单元格；系统检索的字符偏移仍按原text字段严格检查。31项评分回归覆盖错误金额、缺失范围/指标、基期与分组、CSV多行记录、范围泄漏、预算保留/失败计数、结果不覆盖及批处理继续。

本次真实结果位于results/step5-offline-20261007与results/step5-live-20261007。测试结果忽略Git，冻结资料和脚本提交Git。运行完整Python回归可加`--basetemp=test/results/pytest-tmp`使临时测试产物也保留在test/。

## 部署与调用保护

默认 Python 与浏览器回归不调用付费模型。调用保护覆盖逐次密码授权、原提交确认、错误限制、并发额度预留、自然小时、未知用量、取消及重启恢复；网页测试同时检查密码不进入草稿、额度禁用及自动刷新。

隔离部署使用独立 Compose 项目、数据库和上传卷，复用只读 BGE 缓存：

```powershell
.\.venv\Scripts\python.exe test/deployment/prepare.py --output test/results/deploy-new/compose.env
docker compose --env-file test/results/deploy-new/compose.env -f compose.yaml -f test/config/compose.deployment.yaml -p newai-step6-test up -d --build --wait
.\.venv\Scripts\python.exe test/deployment/smoke.py --output test/results/deploy-new/smoke.json
```

准备脚本生成测试凭据并关闭生成模型；只允许 smoke 写入本机 8081/8082。验收实际 CSV/PDF 上传、下载哈希、字段映射、指标对比、BGE 索引、检索与引用。产物路径须为 test/results 中的新路径。

恢复演练使用固定项目 newai-step6-test 和 newai-step6-restore。目标配置用 prepare.py 指定 `--web-port 8082 --db-port 55732`，然后执行 recovery.py 的 `--source-env`、`--destination-env`、`--smoke`、`--output` 参数。脚本检查容器所属项目和 newai_test 数据库，要求目标数据库为空；先模拟未完成任务与 Token 预留，重启核对保守核销，再恢复数据库、上传文件并比较指标、向量检索、引用、历史和额度。不会覆盖开发数据库。

真实模型网页验收为单独的 `test/deployment/live_web.mjs --run OUTPUT SMOKE_JSON`。仅明确授权计费后使用：为隔离 8082 后端配置真实模型，并保留测试调用密码；最多提交一个任务，不自动重试、不录制 trace。验证密码拒绝、Nginx 实时流、刷新、报告、用量、原文定位和桌面/手机宽度，输出只保存虚构资料报告与受控状态。失败须保留产物并先诊断，不直接重发。

CI 入口按 GitHub 规则位于 `.github/workflows/checks.yaml`，实际检查脚本和配置仍集中在 test/ci 与 test/config。CI 强制关闭真实模型，使用 newai_test；顺序执行代码检查、Python、浏览器与容器验收，避免同一测试数据库的后端并发启动。

CI 保存 JUnit、浏览器汇总、失败 trace 和容器验收结果 7 天，成功与失败均可下载诊断；上传清单不包含配置文件或任何 API 密钥。不设置自动重试掩盖不稳定用例，异步操作的断言必须等待明确的完成状态。

## 提示词回归

`unit/test_agent_prompts.py` 验证需求范围匹配、检索状态、输入精简、有效引用示例和 JSON 格式边界。普通回归继续关闭真实生成调用。

手动真实回归复用六个已经消费的评测用例，保持原模型和参数，使用 `newai_test`，不覆盖原冻结清单或原始评测结果，也不重新调用基线。不能与其他使用该测试库的回归同时运行：

```powershell
.\.venv\Scripts\python.exe test/evaluation/run_prompt_regression.py --run --output test/results/prompt-regression-new --max-calls 48
```

输出目录必须是 `test/results` 内的新目录。每次发送前记录请求，失败也计入上限；单轮最多 64 次，不自动重发任务。`--cases A01 A06` 可定向检查，但通过率的分母仅是选中的用例，不能与完整六题结果混用。结果保存输入与代码哈希、逐次用量、报告、受控事件和原文引用核验，保留失败；不保存模型原始回复、思考内容或密钥。

该回归衡量已知问题是否改善，不代表未见样本准确率。格式和引用校验通过后，结论语义仍需人工复核。
