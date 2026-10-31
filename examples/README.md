# 执行证据索引

| 文件/目录 | 来源与用途 |
|---|---|
| `langgraph-run/` | 当前 LangGraph 1.2.9 实际调用 CLI 生成的13组输入、输出、Trace和控制台进度；动作来自离线 Mock |
| `langgraph-run/summary.json` | 实际状态、退出码、模型调用数、运行ID与节点序列 |
| `langgraph-run/store/` | 实际 JSON 存储；四个请求有已保存方案，其余登记文件不代表保存成功 |
| `test-results-langgraph.txt` | LangGraph重构时39项测试的实际通过记录 |
| `deepseek-run/` | 两条实际DeepSeek API输入、输出、Trace和保存文件；各8次调用，明确标记llm |
| `deepseek-run/summary.json` | 根据已有真实运行文件离线核对得到的费用、时间、失败步骤与保存结果 |
| `test-results-deepseek.txt` | 当前46项离线测试通过记录；新增7项配置/响应测试不调用真实API |
| `test-results-langgraph-initial.txt` | 新增快照筛选测试的一次初始失败记录；原因和修正见评估文档 |
| `offline-run/`、`test-results.txt` | 原生 Python 版本历史证据，未改写，不作为 LangGraph 执行证明 |

Trace 中 `events` 是模型动作和工具观察，`node_events` 是实际进入图节点的时间记录，`metadata` 包含 `mode`、引擎和框架版本。Mock 的 `decision_evidence=none_scripted_actions`，不能证明真实 LLM 动态决策。

当前有两条真实模型 Trace，使用DeepSeek非思考模式，不记录reasoning_content。尚无故障恢复Trace；故障后同一图运行内重试不称为恢复。内存检查点的读取与隔离通过 `tests/test_graph.py` 实际验证。

重新生成使用新目录：

```powershell
python -m examples.generate --output-dir examples/another-langgraph-run
```

生成器不覆盖旧目录。记录中的绝对路径反映执行当时的机器环境；换机器后按 README 重新运行即可。
