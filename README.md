# 内容任务规划 Agent

**小影校招 · 第 2 题** &nbsp;|&nbsp; Python 3.11+ · DeepSeek Tool Calling · SQLite · CLI

帮助运营人员在预算与截止时间内形成有素材依据、可追溯的内容活动方案。模型选择动作，工具返回实际结果，再由模型决定换方案、重试或结束。交付范围为方案保存，制作任务保留为待执行状态。

| 验证与实现 | 当前结果 |
| --- | --- |
| 可执行测试 | **46 / 46**，含新增资料校验与保存前重校验回归 |
| 正式离线场景 | 新版本 **13 / 13**，明确标记 Mock |
| 真实模型场景 | 新版本首轮12/13；仅重跑一次网络失败用例后 **13 / 13** 均有通过记录 |
| 安装成本 | 运行与测试仅用 Python 标准库，无第三方运行依赖 |
| 个人投入 | **约 2.5 小时**，候选人自报累计估算，非独立编码时长 |
| 异常改进 | 2个资料校验缺陷已修复，1个业务保护边界已确认；3组回归通过 |

> 初版因无素材状态检查过窄得到12/13，修正规则后复核13/13；本轮真实首轮12/13的原因是一次网络中断，单用例实际重跑后通过。两次评估与原始Trace分别保留，详见评估文档。

## 评审入口

- [需求](docs/01-requirements.md)、[方案](docs/02-solution.md)、[评估](docs/03-evaluation.md)
- [AI 使用](docs/04-ai-usage.md)、[复盘与用时](docs/05-retrospective.md)
- [离线实际运行汇总](examples/offline/summary.json)
- [真实模型首次汇总](examples/live/summary.json)、[原始记录复核](examples/live/summary-rechecked.json)
- [新版本离线评估](examples/revision-offline/summary.json)、[新版本真实评估与单例重跑](examples/revision-live/summary-with-retry.json)
- [逐项要求对齐检查](docs/06-alignment.md)
- [模块理解与设计取舍](docs/07-understanding.md)
- [异常定位练习与分析模板](docs/08-debugging-lab.md)
- [异常根因、修复与回归记录](docs/09-debugging-resolution.md)

建议评审顺序：先看 [需求与验收](docs/01-requirements.md)，对照 [正常路径](examples/live/normal.json) 和 [超预算重规划](examples/live/replan.json)，再阅读 [结果与限制](docs/03-evaluation.md) 及 [AI 使用与贡献](docs/04-ai-usage.md)。

## 运行

Python 3.11+，本机实测 3.12.7。仅使用标准库，**无需安装第三方依赖**。所有命令在项目根目录执行。

```powershell
python run.py --help
python run.py --provider mock --input examples/inputs/replan.json --output runtime/mock.json
```

便于查看的交互式CLI演示：`python scripts/demo.py --interactive`。启动先展示超预算重规划，再提供正常、缺信息、工具失败、预算无解、保存失败和防重复菜单；默认明确使用Mock，可在菜单切换真实DeepSeek。

离线预期：12 超过预算 10，改为 8 的方案，复用脚本后保存。此路径使用规则模拟器，报告标记 `mock:true`。

## DeepSeek 真实运行

首次使用时复制 `.env.example` 为 `.env`，填入本地密钥。已有 `.env` 时直接编辑，避免覆盖。

```powershell
Copy-Item .env.example .env
notepad .env
```

```dotenv
DEEPSEEK_API_KEY=你的密钥
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
DEEPSEEK_TIMEOUT=60
```

默认模型依据 2026-10-28 查阅的[官方文档](https://api-docs.deepseek.com/)；账号实际模型名不同可修改。采用[Chat Completions 工具调用](https://api-docs.deepseek.com/api/create-chat-completion/)，关闭 thinking。环境变量优先于 `.env`。密钥不进入报告，`.env` 已加入 Git 忽略。

```powershell
python run.py --provider deepseek --input examples/inputs/normal.json --output runtime/live-normal.json
python run.py --provider deepseek --input examples/inputs/replan.json --output runtime/live-replan.json
python scripts/evaluate.py --provider deepseek
```

最后一条实际调用 API 并生成 `examples/live/` 下的全场景证据，会产生账号费用。API 失败返回 `incomplete`，不会自动切换 Mock。真实路径可能不同于离线预设，检查失败会如实保留。

## 验证

```powershell
python scripts/verify.py
python scripts/evaluate.py --provider mock
```

第一条保存实际测试日志到 `examples/test-results.txt`。第二条生成 `examples/offline/*.json`，覆盖全部必验场景和截止时间、步数限制、续跑。测试与离线评估均无需网络或密钥。

## 故障、重复与续跑

```powershell
python run.py --provider mock --input examples/inputs/tool_failure.json --fail generate_script:1 --output runtime/failure.json
python run.py --provider mock --input examples/inputs/save_failure.json --fail save_plan:30 --output runtime/save-failure.json
python run.py --provider mock --input examples/inputs/save_failure.json --output runtime/resumed.json
python run.py --provider mock --input examples/inputs/replan.json --output runtime/repeated.json
python run.py --provider mock --input examples/inputs/normal.json --db runtime/limit.sqlite --max-steps 2 --output runtime/limit.json
python run.py --provider mock --input examples/inputs/normal.json --db runtime/limit.sqlite --output runtime/limit-resumed.json
```

相同请求重复执行返回原方案，不重复调用模型或保存；失败可使用原请求续跑。默认数据库为 `runtime/<provider>.sqlite`；可通过 `--db` 换新数据库。相同 ID 不同输入会拒绝执行，修改预算须使用新 ID。退出码：完成 `0`，未完成或需补充信息 `2`。

## 数据与状态

输入示例在 `examples/inputs/`：主题 `theme`、模拟预算 `budget`、唯一 `request_id`、带时区的 `start_at` 与 `deadline`。计划基于用户给定起点计算，保证复现。

输出包含任务状态、计划、分镜脚本、来源 ID、费用合计、预计时间、失败原因、历史步骤与 Trace。每步含参数、公开决策依据、返回摘要和后续动作，不保存内部推理。`completed` 代表方案已保存，`production` 仍为 `planned`。

费用耗时来自合成表，按最终计划计费；本地模拟工具重复调用或失败不额外收费。真实 API token 用量单列，不计入业务模拟预算，不代表真实货币结算。

## 异常定位练习

```powershell
python scripts/debug_lab.py --case E01
python scripts/debug_lab.py --case E02
python scripts/debug_lab.py --case E03
```

演练使用专用资料与独立数据库，默认报告写入被Git忽略的 `runtime/debugging/reports/`。首次故障快照保留在 `examples/exercises/`，修复后结果见 `examples/exercises/after/`。E01、E02现在返回明确资料错误并保留成功步骤；E03仍拒绝同一ID下的输入变更。三组回归通过不代表业务请求全部成功，而是异常被正确处理、没有越界保存。新增10项测试已计入46项；原13组正式场景单列。

```text
src/content_planner/  Agent、DeepSeek/Mock、工具、SQLite
tests/                可执行验证与边界测试
examples/             输入、实际输出与 Trace
data/                 合成素材与费用耗时
docs/                 五份考核文档、要求核对、理解说明与异常练习
scripts/              测试记录与场景评估
```

## 提交前

真实运行证据、理解说明、实际分工、自报用时和异常分析修复记录已包含。候选人应审阅确认内容，能依据代码与记录解释取舍；AI完成的分析和修改已明确归属，不作为独立手工开发经历。

- 题号：第 2 题——内容任务规划 Agent
- GitHub 仓库：[mammmm1/xiaoying_agent](https://github.com/mammmm1/xiaoying_agent)
- 最终 commit SHA：在仓库根目录执行 `git rev-parse HEAD`；后续修改提交后须使用更新后的 SHA。

上传范围仅含项目源码、合成数据、文档和验证材料。真实密钥、本地数据库、缓存及个人电脑配置不在提交范围。
