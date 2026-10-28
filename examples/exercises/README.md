# 异常演练首次快照

这些是独立调试场景的实际复现记录，不计入正式13组验收通过率，也不证明真实模型效果。候选人定位和修复尚未完成。

| 文件 | 首次实际结果 |
| --- | --- |
| [E01.json](E01.json) | 外部费率表接入后出现TypeError，没有已保存方案 |
| [E02.json](E02.json) | 素材库更新后生成阶段出现KeyError，没有已保存方案 |
| [E03.json](E03.json) | 首次方案保存成功；同ID修改预算后STATE_CONFLICT，首份方案保留 |

复现与分析模板见 [docs/08-debugging-lab.md](../../docs/08-debugging-lab.md)。后续复现默认写入runtime，保留本目录首次快照。selected_actions是实际离线决策器的动作，checkpoint_steps来自实际SQLite断点；异常路径均为仓库相对路径。
