# 03 · 评估与实际证据

## 已完成

本机 Python 3.12.7；当前可执行测试 **46/46 通过**，包含初版36项与本轮10项资料校验回归。实际运行记录见 [test-results.txt](../examples/test-results.txt)。接口单测使用HTTP替身；下文另有真实DeepSeek调用证据，两者明确区分。

执行 `python scripts/evaluate.py --provider mock`，**13/13 场景通过**，见 [summary.json](../examples/offline/summary.json)。各 JSON 同时包含实际输入、输出、步骤和 Trace；全部标记 mock:true。

| 用例 | 预期 | 实际离线结果与证据 |
| --- | --- | --- |
| 正常 | 可行方案保存 | completed，成本12，[normal](../examples/offline/normal.json) |
| 超预算重规划 | 12 超出10后换方案 | 改选8，脚本只成功生成一次，[replan](../examples/offline/replan.json) |
| 缺预算 | 不猜测、不保存 | needs_input，0次模型调用，[missing_budget](../examples/offline/missing_budget.json) |
| 缺截止 | 明确缺字段 | needs_input，[missing_deadline](../examples/offline/missing_deadline.json) |
| 工具失败 | 实际失败可见、成功结果保留 | 脚本失败后重试，检索结果复用，[tool_failure](../examples/offline/tool_failure.json) |
| 保存持续失败 | 明确未保存 | incomplete，saved:false，[save_failure](../examples/offline/save_failure.json) |
| 预算无解 | 不越界保存，说明缺口 | 最低成本5，预算4，缺口1，[over_budget](../examples/offline/over_budget.json) |
| 截止重规划 | 45分钟内 | basic预计36分钟，[deadline_replan](../examples/offline/deadline_replan.json) |
| 截止无解 | 终止不保存 | 最低档仍差6分钟，[deadline_impossible](../examples/offline/deadline_impossible.json) |
| 无资料 | 不编造素材 | incomplete，[no_assets](../examples/offline/no_assets.json) |
| 重复执行 | 一份方案 | 新增模型调用0，数据库仅一行，[duplicate](../examples/offline/duplicate.json) |
| 步数上限 | 到限未完成 | 2轮后STEP_LIMIT，[step_limit](../examples/offline/step_limit.json) |
| 续跑 | 复用成功步骤 | 保留脚本并保存，[resume](../examples/offline/resume.json) |

normal 第3步估算 feasible:true 后保存；replan 第3步 feasible:false、budget_gap:2 后再次估算，成本变为8。Trace 每步包含参数、decision_basis、observation_summary、next_action。**这两条是规则模拟器的实际流程记录，不是已完成的真实模型决策证据。**

## 真实 DeepSeek 实测

2026-10-28初次网络失败的历史记录保留在 [initial-network-failure.json](../examples/live/initial-network-failure.json)，未用作真实决策证据。后续用户要求重试后连接成功，执行 `python scripts/evaluate.py --provider deepseek` 完成13组真实模式场景。请求与响应模型均为 **deepseek-flash**。本轮合计prompt_tokens=105610、completion_tokens=5847、total_tokens=111457；这是用量而非结算金额。

| 真实场景 | 实际结果 |
| --- | --- |
| [normal](../examples/live/normal.json) | 估算12、106分钟可行后保存，5轮 |
| [replan](../examples/live/replan.json) | 观察12超预算10，保留脚本，改选standard估算为8后保存，6轮 |
| [tool_failure](../examples/live/tool_failure.json) | 脚本首次失败后模型选择重试，未重复检索，最终保存，7轮 |
| [save_failure](../examples/live/save_failure.json) | 两次保存失败后incomplete、saved:false；成功脚本和估算保留 |
| [over_budget](../examples/live/over_budget.json) | 无可行方案，展示预算缺口，不保存 |
| [deadline_replan](../examples/live/deadline_replan.json)、[deadline_impossible](../examples/live/deadline_impossible.json) | 时间可行时换档次保存，无解时停止 |
| [missing_budget](../examples/live/missing_budget.json)、[missing_deadline](../examples/live/missing_deadline.json) | needs_input，0次模型调用，不猜测输入 |
| [duplicate](../examples/live/duplicate.json) | 返回原方案，0次新增模型调用，初次数据库实测仅一行记录 |
| [step_limit](../examples/live/step_limit.json)、[resume](../examples/live/resume.json) | 到限明确未完成，复用脚本继续后保存 |
| [no_assets](../examples/live/no_assets.json) | 两次空检索，主动改写查询后请求补充素材，needs_input，未生成或保存 |

真实调用步骤包含API响应ID、参数、公开动作依据、实际观察与后续动作。已人工核对正常/重规划分歧点、失败后的重试与停止、成功结果保留与无资料查询改写。

## 评估器问题与修正

首次自动检查 **12/13**，见未改动的 [summary.json](../examples/live/summary.json)。第2题必验全通过。额外no_assets用例原来只接受incomplete，模型在空检索后合理请求补充并返回needs_input，因此被过窄的状态预期误判。

修正后允许明确追问或说明缺口后停止，同时增加更强检查：必须实际观察到空检索、说明资料缺口、没有编造脚本或保存；补强保存失败、预算/时间无解和步数上限的实际观察要求，避免网络失败被误判为业务失败用例通过。新增2项可执行测试验证这些规则。

运行 `python scripts/recheck.py examples/live`，对同批原始报告复核 **13/13**，见 [summary-rechecked.json](../examples/live/summary-rechecked.json)。未调用API、未修改原始Trace，复核保留源文件SHA256；数据库防重复条数沿用首次实测结果，没有从报告文本重新推断。此次修正解决评估口径问题，不宣称提高模型能力。

## 失败与修正

首轮26项测试中24项通过、2项因系统临时目录不可写失败；改用项目下runtime/test-temp后修复。初版补齐接口边界和评估规则后达到36项；本轮新增10项资料契约回归，总计46项通过。环境与评估修正不等同于模型效果提升。

对抗模型替身验证：未保存却声称完成、伪造费用、未知工具、未检索素材、旧估算、多动作批次、纯文本宣称成功均不能绕过约束。真实sqlite OperationalError注入显示STORAGE_ERROR，不把失败写成成功。

## 追加异常与当前版本验证

原E01费率类型错误和E02素材facts缺失的TypeError/KeyError现已修复，分别返回INVALID_RATES和INVALID_ASSET_DATA，带不可原样重试标记，保留成功结果且不越界保存。E03的同ID输入变更为正确保护，保留原方案；测试验证新ID可以接受新预算。异常回归 **3/3**，见 [修复后报告](../examples/exercises/README.md) 与 [分析记录](09-debugging-resolution.md)。

当前版本正式离线评估 **13/13**，见 [revision-offline/summary.json](../examples/revision-offline/summary.json)。原版offline和live记录不覆盖。

新版本真实模式首轮 **12/13**，见 [revision-live/summary.json](../examples/revision-live/summary.json)。唯一未通过项deadline_replan在第5轮模型请求发生网络/超时错误；前4轮检索、脚本、premium和standard估算成功，未保存方案。这不是业务成功，原始失败完整保留。与初版无素材状态检查问题不同，本次没有调整预期，单用例真实重跑结果另存retries目录。

仅对该用例使用新数据库实际重跑，单次API超时上限设为120秒；这次是完整重跑，不声称复用了已被评估器清理的临时数据库断点。模型观察到premium和standard超时后改选basic，成本5、总时间36分钟，完成保存。其他已通过用例没有重复运行。

当前13个用例均有通过记录，见 [summary-with-retry.json](../examples/revision-live/summary-with-retry.json) 与 [截止时间重跑Trace](../examples/revision-live/retries/deadline_replan.json)。汇总明确首轮12/13及单例重跑，记录源文件哈希，不把它表述成单轮13/13或长期成功率。

## 限制

- 已覆盖本轮资料契约缺陷；未做全量模糊测试或所有第三方资料格式兼容。文件缺失、部署与存储故障仍应结合CLI及实际运行环境排查。
- 真实效果仅来自少量合成资料评估，尚未验证长期稳定性；本轮的一次网络中断表明真实调用仍受服务和网络状态影响。
- 关键词检索、脚本模板和费用耗时均为本地合成实现，不评价开放式创作质量。
- 制作步骤只被规划，不实际执行；不提供按调用收费工具的真实账本。
- 串行时间估算，不含人力、节假日或并行资源约束。
- SQLite保存唯一性不等于生产并发续跑安全；checkpoint未加跨执行器锁。
- 故障注入在副作用前，不覆盖事务提交结果不明等生产故障。
- API token记录不等于结算金额；本次账号可调用，不代表未来余额、限流和服务可用性。
