# 真实模型证据

2026-10-28已恢复连接并完成13组真实模式场景，请求与响应模型为deepseek-flash。normal、replan等报告含API响应ID、实际工具结果与完整Trace。业务工具仍为合成资料和本地函数，只有决策使用真实模型。

summary.json保留首次检查12/13。无素材时模型两次空检索后主动追问，合理返回needs_input；原规则只接受incomplete，因此误判。修正规则并强化观察证据检查后，同批原始记录复核13/13，见summary-rechecked.json，带源文件SHA256，未修改Trace或再次调用API。初次数据库防重复条数检查沿用首次实测结果。

initial-network-failure.json保留更早网络失败历史，不作为业务决策证据。输入缺字段直接校验，重复请求返回原方案，这些报告本次模型调用为0，不伪称额外模型调用。

重新调用API评估：

```powershell
python scripts/evaluate.py --provider deepseek
```

只复核已有报告而不调用API：`python scripts/recheck.py examples/live`。重跑会覆盖同名报告，应先保存需要保留的旧证据。不得复制offline日志到本目录冒充真实结果。
