# 06 · 与考核第 2 题的对齐检查

结论：核心代码、正式离线验证和真实模型决策证据已覆盖要求。首次真实检查12/13，修正规则并强化证据检查后同批原始记录复核13/13。已补充实际分工、约2.5小时自报投入、模块理解、异常根因及修复记录；本轮分析和修改由AI执行。GitHub信息见下表，SHA以实际HEAD为准。

当前修复版46项测试与3组异常回归通过，正式离线13/13；真实首轮12/13，唯一网络失败场景单例实际重跑后通过。当前13个真实用例均有通过证据，首轮失败及重跑分别保留，见revision-live/summary-with-retry.json。

| 题目要求 | 实现或证据 | 当前状态 |
| --- | --- | --- |
| 主题、预算、截止时间输入 | examples/inputs与输入校验；额外显式提供计划起点 | 已实现 |
| 合成素材与费用耗时表 | data/assets.json、rates.json | 已提供 |
| 素材检索、脚本生成、估算、保存工具 | src/content_planner/tools.py | 已实现，本地模拟业务工具 |
| 任务计划、步骤状态、工具结果、总费用与保存结果 | plan、steps、trace、latest_estimate、saved | 已实现 |
| 模型动作→工具→观察→模型调整或结束 | agent.py与DeepSeek原生tool calls | 已实跑，live下有Trace和响应ID |
| 两条不同执行路径 | live/normal.json与replan.json | 正常保存；超预算后保留脚本，改选8再保存 |
| 正常完成 | live/normal.json | 真实通过 |
| 缺预算或截止时间 | live/missing_budget.json、missing_deadline.json | 真实模式输入校验通过，无需调用模型 |
| 工具失败 | live/tool_failure.json、save_failure.json | 真实通过，失败状态保留 |
| 超预算调整或无解停止 | live/replan.json、over_budget.json | 真实通过 |
| 重复执行 | live/duplicate.json、SQLite唯一键 | 真实通过 |
| 保留成功步骤与失败续跑 | live/resume.json与可执行测试 | 真实通过，加分项已实现 |
| 步数或调用上限 | 默认12轮，1~30可配；offline/step_limit.json | 已实现，到限明确未完成 |
| 至少3组实际输入输出Trace | examples/live下13组报告 | 真实模式报告已提供，Mock另行保留 |
| 代码、测试、例子、data、五份文档、依赖、env示例 | 项目对应目录 | 已齐备 |
| AI使用、个人贡献、用时和改进 | docs/04、05、07、08、09 | 已记录分工、自报约2.5小时、理解和实际异常修复；AI工作归属明确 |
| 题号、GitHub URL和最终SHA | 第2题；[mammmm1/xiaoying_agent](https://github.com/mammmm1/xiaoying_agent)；`git rev-parse HEAD` | 提交时使用实际最终HEAD |

演示采用题目允许的CLI；运行 `python scripts/demo.py --interactive` 可查看动作参数、观察、后续动作、脚本和最终状态。默认Mock；菜单可显式切换DeepSeek，不把Mock冒充真实决策。没有界面不影响题目规定的交付方式。
