# LangGraph 评估与限制

## 实际执行结果

2026-10-30，Windows、Python 3.11.7、LangGraph 1.2.9、langgraph-checkpoint 4.1.1。保留原26项自动化测试，新增13项图测试，共39项全部通过，实际输出见 [test-results-langgraph.txt](../examples/test-results-langgraph.txt)。

随后接入DeepSeek，增加7项配置加载、参数、空白/截断响应及CLI优先级测试，当前共46项离线测试通过，见 [test-results-deepseek.txt](../examples/test-results-deepseek.txt)。该测试集不调用真实API；真实运行证据单独记录如下。

2026-10-31另在新建隔离环境与不含.env的源码快照中复现46项测试、README离线命令、13组CLI用例及现有真实材料离线核对，均通过。结果与安装时修正的问题见[交付检查](06-delivery-check.md)。

实际运行 `python -m examples.generate --output-dir examples/langgraph-run` 生成13组 CLI 证据。每组包含 input、output、trace、console.log；[summary.json](../examples/langgraph-run/summary.json) 记录命令、状态、退出码、模型调用数和节点序列。Trace 的 metadata.engine 为 langgraph，mode 为 mock。

| 场景与 Trace | 实际结果 |
|---|---|
| [normal](../examples/langgraph-run/normal.trace.json) | completed，8单位、360分钟，方案实际写入 |
| [missing_budget](../examples/langgraph-run/missing_budget.trace.json)、[missing_deadline](../examples/langgraph-run/missing_deadline.trace.json) | needs_input，prepare→finalize，零模型调用 |
| [budget_replan](../examples/langgraph-run/budget_replan.trace.json) | 12→8单位；第二版重新估算后保存 |
| [tool_failure](../examples/langgraph-run/tool_failure.trace.json) | 估算首次失败，保留已有脚本，再估算并保存，未重复生成脚本 |
| [duplicate](../examples/langgraph-run/duplicate.trace.json) | completed，零模型/工具调用，原保存文件字节不变 |
| [conflict](../examples/langgraph-run/conflict.trace.json) | conflict，零模型调用，原文件不变 |
| [infeasible](../examples/langgraph-run/infeasible.trace.json) | incomplete，费用缺口4，时间缺口120分钟 |
| [time_exceeded](../examples/langgraph-run/time_exceeded.trace.json) | 预算通过但超时60分钟，保存被拒绝 |
| [time_replan](../examples/langgraph-run/time_replan.trace.json) | 360→150分钟，重新估算后保存 |
| [save_failure](../examples/langgraph-run/save_failure.trace.json) | incomplete，saved=false，没有虚假成功方案 |
| [step_limit](../examples/langgraph-run/step_limit.trace.json)、[model_limit](../examples/langgraph-run/model_limit.trace.json) | incomplete，业务步数/模型调用各限制为1 |

`langgraph-run/store/` 中只有四个请求具有非空 saved_plan，其余请求登记文件不代表方案保存成功。原生版本的 `offline-run/` 与 `test-results.txt` 保留为历史，不混作框架运行证据。

## 新增图测试的预期与验证

`tests/test_graph.py` 实际验证：

1. 对象为 CompiledStateGraph，节点与条件边符合设计。
2. 正常节点序列及 reducer 追加行为，没有重复步骤和 Trace。
3. 缺输入直接收尾，无模型或工具执行。
4. 超预算估算后的待执行节点仍是 decision，图没有选择低价方案。
5. 工具失败快照保留已有方案，回到模型节点。
6. 修改方案的检查点中旧估算为空。
7. 无效动作经 decision 自循环，受业务上限控制。
8. 最后一次允许动作可以保存，不提前截断。
9. 模型主动结束路由到 finalize，不调用工具。
10. 最终检查点及历史快照可实际读取，包含待执行工具动作。
11. 同一业务请求的不同图运行使用不同 ID，原检查点不被覆盖，JSON 幂等仍有效。
12. 新建内存 Saver 没有旧检查点。
13. 图递归兜底返回 incomplete 并保留最后方案。

工具费用与时间、保存失败、实际文件写入失败、版本校验、状态保留、幂等和结束声明等业务检查仍由原26项测试覆盖。

## 初始失败与修正

初始图回归有1项测试失败，记录见 [test-results-langgraph-initial.txt](../examples/test-results-langgraph-initial.txt)。检查点历史按最新在前返回，后续替代素材检索仍保留旧的超预算估算；测试只用“超预算且下一节点为decision”筛选，因此误选检索后的快照。

修正测试为同时要求最新观察来自 estimate_plan，继续检查初始素材未被路由自动替换。没有通过清除合法估算来迁就测试。修正后39项全部通过。

## 检查点验证边界

已验证快照读取、历史、状态隔离；**没有暂停/恢复执行测试，不声称断点恢复或故障恢复**。InMemorySaver 不跨进程持久化。故障后模型决定下一步是本次图运行内继续执行，不等于恢复被终止的进程。LangGraph 检查点不替代业务 JSON 幂等。

## 已执行的DeepSeek真实模型验证

2026-10-30实际请求 `https://api.deepseek.com/chat/completions`，使用 `deepseek-flash`、`thinking=disabled`、JSON输出、每次最多1200输出tokens。两次任务均以真实模型驱动同一个LangGraph，没有使用Mock动作。Trace包含服务响应ID、模型、usage、动作、参数和工具观察，不含reasoning_content或密钥。

| 输入及记录 | 实际决策路径 | 结果 |
|---|---|---|
| [normal输入](../examples/deepseek-run/normal.input.json)、[Trace](../examples/deepseek-run/normal.trace.json)、[输出](../examples/deepseek-run/normal.output.json) | 检索M01→选择不兼容quick_post被拒绝→修正campaign_video→估算12单位超预算→补充检索→M03 quick_post→重新估算4单位→保存 | completed；8次调用；11.0秒；最终150分钟 |
| [故障输入](../examples/deepseek-run/tool_failure.input.json)、[Trace](../examples/deepseek-run/tool_failure.trace.json)、[输出](../examples/deepseek-run/tool_failure.output.json) | 检索→生成M01方案→估算注入一次失败→直接重试估算并复用原脚本→12单位超预算→检索替代→M03 quick_post→估算并保存 | completed；8次调用；13.062秒；最终150分钟 |

第一条没有注入故障，仍出现模型选错素材形式；记录未删除。第二条故障发生后，紧接着调用estimate_plan，期间没有重新generate_script；已成功的初始脚本仍保留在steps及保存记录中。两次都在预算10、截止8小时内保存第二版方案，没有修改用户约束。

第一条业务上限为8步/8次模型调用，最后一次允许动作成功保存；第二条上限12，实际8次结束。累计16次API调用，服务返回total_tokens合计37209，包含重复上下文；不是活动预算费用，也没有推算成实际货币账单。

`python -m examples.verify_live --run-dir examples/deepseek-run` 已离线核对两条输出与实际保存文件、方案指纹、版本、费用/时间合计、双约束及调用上限，结果见 [summary.json](../examples/deepseek-run/summary.json)。该核对脚本不生成模型动作、不改写Trace，也不发起API调用。

这两条真实路径提供了不同工具反馈下动作调整的证据，但样本量不足以代表模型普遍可靠。原13条Mock记录仍仅证明流程；HTTP stub测试仍不属于真实模型证据。

## 其他限制

关键词检索、本地模板脚本和串行合成耗时保持原范围；没有并发写入锁。只实测了DeepSeek的两次任务，其他供应商兼容性、真实时间约束调整、大样本成功率和实际营销效果尚未评估。Trace 的绝对路径反映本次机器环境，复现时使用新证据目录与请求ID。
