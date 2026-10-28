# 异常演练首次快照

本目录根部文件是修复前的首次快照，保持不变；修复后结果在after目录，分析与修改由AI协助完成。演练不计入正式13组场景通过率，也不证明真实模型效果。

| 文件 | 首次实际结果 |
| --- | --- |
| [E01.json](E01.json) | 外部费率表接入后出现TypeError，没有已保存方案 |
| [E02.json](E02.json) | 素材库更新后生成阶段出现KeyError，没有已保存方案 |
| [E03.json](E03.json) | 首次方案保存成功；同ID修改预算后STATE_CONFLICT，首份方案保留 |

复现与分析模板见 [docs/08-debugging-lab.md](../../docs/08-debugging-lab.md)。后续复现默认写入runtime，保留本目录首次快照。selected_actions是实际离线决策器的动作，checkpoint_steps来自实际SQLite断点；异常路径均为仓库相对路径。

## 修复后回归

| 文件 | 本轮实际结果 |
| --- | --- |
| [after/E01.json](after/E01.json) | 结构化INVALID_RATES，明确incomplete，保留已生成脚本，不保存 |
| [after/E02.json](after/E02.json) | 结构化INVALID_ASSET_DATA，保留检索结果，无部分脚本或保存 |
| [after/E03.json](after/E03.json) | STATE_CONFLICT保持，首次方案与单条数据库记录保留 |

3组回归检查均通过，源码不再以未捕获异常处理E01/E02的已知资料问题。根因与修改范围见 [docs/09-debugging-resolution.md](../../docs/09-debugging-resolution.md)。
