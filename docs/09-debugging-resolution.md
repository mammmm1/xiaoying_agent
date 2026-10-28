# 09 · 异常根因、修复与回归

本轮分析、实现和自动化验证由AI协助完成。候选人自报投入仍为约2.5小时，没有据此增加独立诊断或手工改代码经历。首次故障快照保留，新结果单独记录。

## E01：外部费率类型异常

**复现**：`python scripts/debug_lab.py --case E01`。专用资料把 `production.standard.cost` 写为文本 `"5"`，预算为10。

**修复前**：检索、脚本生成和premium估算成功；选择standard后，`estimate_plan`直接将文本费用与数字求和，触发TypeError，未形成正常工具观察。模型参数中的tier合法，因此仅校验模型输入无法发现外部费率问题。

**定位与修改**：[tools.py](../src/content_planner/tools.py) 中新增 `rate_parts` 和数值校验，检查当前档次与固定步骤的配置、费用、分钟数、币种和版本。拒绝文本、布尔、负数、NaN、Infinity、不可表示的合计与日期。资料错误返回 `INVALID_RATES`、字段位置和 `retryable:false`，经原有工具反馈循环交还模型。没有自动把文本转换为费用，避免隐去上游契约错误。

**修复后**：离线Mock观察到standard费率错误，明确incomplete，保留检索、脚本和此前估算，没有保存。真实模型仍可选择合法其他档次；不可重试标志只说明同一参数与资料下的重试无效，不强制所有替代动作停止。

**保存边界**：save_plan仍调用同一估算逻辑。即使先前估算成功，保存前费率变为非法，仍拒绝提交，不能依赖旧结果绕过校验。

证据：[首次快照](../examples/exercises/E01.json)、[修复后记录](../examples/exercises/after/E01.json)。

## E02：检索到的素材缺少事实字段

**复现**：`python scripts/debug_lab.py --case E02`。A01包含id、标题和标签，可被检索，但没有facts。

**修复前**：检索成功并将资料放入断点；`generate_script`直接读取facts，引发KeyError。检索命中只证明关键词匹配，不证明素材足以生成有依据的脚本。

**定位与修改**：[tools.py](../src/content_planner/tools.py) 在检索阶段检查元数据类型和唯一ID，在生成阶段检查选中素材的facts必须为非空文本列表。使用数据前完成校验，不生成部分脚本，也不把字符串误按字符拼接。返回 `INVALID_ASSET_DATA`、素材ID、字段缺口和 `retryable:false`。

**修复后**：离线Mock返回明确未完成，保留检索结果，无已保存方案。校验针对所选素材，模型可在观察错误后改选事实完整的A03；测试验证这一替代仍可生成脚本。系统不会为缺失事实补写未经支持的内容。

证据：[首次快照](../examples/exercises/E02.json)、[修复后记录](../examples/exercises/after/E02.json)。

## E03：同请求编号修改预算

**复现**：`python scripts/debug_lab.py --case E03`。先按预算10保存，再用同一request_id把预算改为11。

**判断**：这是合理业务保护。`Store.begin`将ID与请求指纹绑定，输入变化后返回STATE_CONFLICT，避免新需求覆盖旧方案。拒绝的是变更后的请求，数据库内仍保留首次已保存方案，因此冲突报告的saved:false与数据库存在旧方案并不矛盾。

**处理**：保留原实现，不为了让所有请求成功而放松指纹约束。需求发生变化应使用新ID。新增测试确认新ID和预算11可创建另一方案，原方案保持不变，数据库总共两条记录。

证据：[首次快照](../examples/exercises/E03.json)、[本轮回归](../examples/exercises/after/E03.json)。

## 验证范围

| 验证 | 实际结果 |
| --- | --- |
| 全部可执行测试 | 46/46；新增10个测试方法，覆盖多种坏资料、溢出、保存重校验、有效素材替代和新请求ID |
| 三组异常回归 | 3/3；E01/E02明确拒绝坏资料，E03保留合理保护与首份记录 |
| 新版本正式离线场景 | 13/13，见revision-offline报告 |
| 新版本真实场景 | 首轮12/13；唯一网络失败用例实际单独重跑后，13个用例均有通过记录，原始失败保留 |

测试日志：[test-results.txt](../examples/test-results.txt)。新增测试：[test_data_validation.py](../tests/test_data_validation.py)。异常回归成功表示达到了正确失败、保留结果或拒绝冲突的目标，不表示所有业务请求都已完成。

本次不修改首次故障快照、初版真实Trace或其哈希。重复演练默认写入runtime；可用 `--output-dir` 指定新的对照目录。

新版本真实记录：[首轮汇总](../examples/revision-live/summary.json)、[单例重跑后的证据汇总](../examples/revision-live/summary-with-retry.json)。本轮网络重跑没有修改业务预期，使用新数据库，不将其描述成断点续跑或单轮满分结果。
