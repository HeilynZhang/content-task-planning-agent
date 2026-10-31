# 内容任务规划 Agent

小影科技 AI Agent 笔试第 2 题。Python CLI + 本地 JSON + 可替换模型接口，使用 LangGraph StateGraph 管理执行流程，无前端或数据库服务。

**当前状态：离线测试和两条 DeepSeek 真实模型路径均已执行。** 46项离线测试通过；真实调用记录了不兼容参数修正、超预算重规划和工具故障后重试。真实 Trace 在 `examples/deepseek-run/`，Mock 记录保留独立目录，两条样例不代表普遍成功率。

## 环境与安装

Python 3.10+；本次实际测试使用 Windows、Python 3.11.7、LangGraph 1.2.9 和 langgraph-checkpoint 4.1.1，版本锁定在 `requirements.txt`。本机已有这些版本；其他环境在项目根目录安装：

```powershell
python -m pip install -r requirements.txt
```

## 一分钟离线运行

```powershell
python -m src.cli --mode mock --topic "虚构新品推广" --budget 10 --deadline-hours 8 --request-id demo-001 --trace-file data/runtime/demo.trace.json --output-file data/runtime/demo.output.json
```

预期生成 M02 标准图文计划：预算 8 单位，串行耗时 6 小时，满足输入要求并实际保存。再次执行相同命令会复用已保存方案；相同 ID 改预算会返回冲突。需要重新规划时使用新 ID。

进度输出到 stderr，最终 JSON 输出到 stdout。`--output-file` 保存最终结果，`--trace-file` 单独保存 Trace；不传 Trace 路径时它包含在最终 JSON 中。输出采用 UTF-8。

最终结果中的 `current_plan` 是业务活动计划，含素材来源、模板脚本以及业务步骤、依赖、费用和预计分钟数；`steps` 和 Trace 是 Agent 的工具执行记录。规划保存成功时，业务步骤仍为 `planned`，没有真正制作或发布内容。

## 其他场景

```powershell
# 预设超预算流程：12→8；Mock 动作本身不证明模型会重规划
python -m src.cli --mode mock --mock-scenario budget_replan --topic "虚构新品推广" --budget 10 --deadline-hours 8 --request-id demo-budget

# 注入一次估算故障，复用已生成脚本再次估算
python -m src.cli --mode mock --mock-scenario tool_retry --fail-tool-once estimate_plan --topic "虚构新品推广" --budget 10 --deadline-hours 8 --request-id demo-failure

# 缺少预算：needs_input，不调用模型或保存方案
python -m src.cli --mode mock --topic "虚构新品推广" --deadline-hours 8 --request-id demo-missing

# 预算满足但时间超限，保存工具会拒绝
python -m src.cli --mode mock --mock-scenario time_exceeded --topic "虚构新品推广" --budget 10 --deadline-hours 5 --request-id demo-time
```

也支持 `--input examples/langgraph-run/normal.input.json`。单独 CLI 参数覆盖 JSON 中对应字段。只支持四个业务输入字段，未知字段会提示补充/修正，避免忽略硬性要求。

退出码：`0` 已保存/复用，`2` 需要补充信息、冲突或运行未完成，`1` 配置、输入文件或启动错误。

## DeepSeek 真实模型接入

复用 OpenAI 兼容 Chat Completions 接口，已实际使用 `https://api.deepseek.com`、`deepseek-flash`、非思考模式完成测试。配置示例在 `.env.example`，密钥只填本地 `.env`；使用 `--env-file .env` 显式加载。该文件已被 `.gitignore` 排除，示例文件只保留占位值。

```powershell
# 首次使用时复制，然后在本地编辑 .env 中的 LLM_API_KEY
if (!(Test-Path .env)) { Copy-Item .env.example .env }

python -m src.cli --env-file .env --mode llm --topic "虚构新品推广" --budget 10 --deadline-hours 8 --request-id deepseek-new-normal-001 --max-steps 12 --max-model-calls 12 --trace-file data/runtime/deepseek-normal.trace.json --output-file data/runtime/deepseek-normal.output.json

python -m src.cli --env-file .env --mode llm --topic "虚构新品推广" --budget 10 --deadline-hours 8 --request-id deepseek-new-failure-001 --fail-tool-once estimate_plan --max-steps 12 --max-model-calls 12 --trace-file data/runtime/deepseek-failure.trace.json --output-file data/runtime/deepseek-failure.output.json
```

重新采集模型决策要使用新的 request_id 和输出文件，缓存复用不能算新路径。`.env` 只支持 KEY=VALUE、整行注释与可选配对引号，不支持 shell 表达式；已有进程环境变量优先，显式 CLI 上限参数优先于配置文件。

也可以直接配置进程环境变量，不传 `--env-file`：

```powershell
$env:LLM_BASE_URL="https://api.deepseek.com"
$env:LLM_MODEL="deepseek-flash"
$env:LLM_API_KEY="your-local-key"
$env:LLM_THINKING="disabled"

python -m src.cli --mode llm --topic "虚构新品推广" --budget 10 --deadline-hours 8 --request-id live-normal-001 --store-dir data/runtime/live --trace-file examples/live-normal.trace.json --output-file examples/live-normal.output.json

python -m src.cli --mode llm --topic "虚构新品推广" --budget 5 --deadline-hours 3 --request-id live-tight-001 --store-dir data/runtime/live --trace-file examples/live-tight.trace.json --output-file examples/live-tight.output.json

python -m src.cli --mode llm --topic "虚构新品推广" --budget 10 --deadline-hours 8 --request-id live-failure-001 --fail-tool-once estimate_plan --store-dir data/runtime/live --trace-file examples/live-failure.trace.json --output-file examples/live-failure.output.json
```

以上是复现入口，已提交的实际记录见 [DeepSeek汇总](examples/deepseek-run/summary.json)。模型自行选择动作，不保证复跑出现相同路径。真实证据有 `metadata.mode=llm`、实际 API 调用、响应ID、usage 和连续工具观察；仅记录动作与简要依据，不记录 reasoning_content。

程序在 `LLM_BASE_URL` 后追加 `/chat/completions`。支持 `LLM_TIMEOUT_SECONDS`（默认45）、`LLM_MAX_TOKENS`（默认800）、`LLM_JSON_MODE`（默认1）和可选 `LLM_THINKING`（留空不发送，或 enabled/disabled）。当前 DeepSeek 示例使用60秒、1200 tokens、JSON模式和 disabled；其他服务不支持 thinking 时应留空。空白或截断响应明确报错，无自动重试。

官方参考：[接口与模型名称](https://api-docs.deepseek.com/)、[JSON输出](https://api-docs.deepseek.com/guides/json_mode/)、[思考模式参数](https://api-docs.deepseek.com/guides/thinking_mode/)。

默认模式为 `llm`，缺配置会明确失败，不会悄悄回退 Mock。没有 API 时请显式使用 `--mode mock`。

## LangGraph 工作流

原来的 Python 手写循环已替换为编译后的 `StateGraph.invoke()`。四个节点为 `prepare`（校验与幂等）、`decision`（模型选择动作）、`execute_tool`（执行一个工具并更新观察）、`finalize`（整理结果）。Conditional Edges 根据动作类型与终止状态路由，工具失败或约束不满足会回到模型；边不选择替代素材或固定重试策略。

`GraphState` 在 `src/state.py` 中定义，步骤、错误和 Trace 使用追加 reducer，其他字段覆盖更新。节点返回增量更新，不直接修改输入状态。原有工具和模型接口继续复用；`TaskState` 仅作为工具调用时的临时兼容结构。

`InMemorySaver` 保存本进程中的图状态，使用独立 `run_id` 作为 `thread_id`，与业务幂等的 `request_id` 分开。已测试读取状态历史与检查点隔离；**没有实现暂停恢复或跨进程故障恢复**。进程退出后检查点丢失，Mock 游标、模型对象和故障注入标记也不在图状态中，不应据此宣称恢复能力。

学习入口：[图设计](docs/02-solution.md) → [节点实现](src/agent.py) → [图状态](src/state.py) → [图测试](tests/test_graph.py)。Trace 增加实际节点事件，元数据记录引擎、框架版本和运行 ID。

## 限制与保存

- 真实模型每轮获得当前状态和工具结果，自主返回工具动作及参数或结束；Python 无超预算自动换方案或失败自动重试策略。
- 工具参数和保存条件由 Python 校验。修改方案使旧估算失效，保存时按规则重新核对，预算与时间同时满足才能成功。
- `--max-steps`、`--max-model-calls` 默认均为12，也可通过 `MAX_AGENT_STEPS`、`MAX_MODEL_CALLS` 配置。无自动 HTTP 重试；到达上限返回 `incomplete`。
- 业务步数与图节点数分开统计。最后一次额度内选出的工具仍执行；另设派生的图递归上限兜底，异常时读取最近检查点返回未完成，不自动恢复或重跑工具。
- 费用为最终活动计划的业务费用；Agent 调用与真实 API 费用不计入活动预算。耗时为业务步骤串行求和，不是程序运行耗时。
- 有效请求首次运行即绑定 ID 与输入，失败后换需求也要新 ID。保存文件默认位于 `data/runtime/`，使用临时文件加原子替换。只保证单进程顺序使用，不支持并发写入锁。
- 保存失败不标记成功。失败后重跑会保留请求绑定，但不支持跨进程恢复中间步骤。

## 测试与证据

```powershell
python -m unittest discover -s tests -v

# 使用一个不存在的新目录，实际调用 CLI 生成13组输入、输出与Trace
python -m examples.generate --output-dir examples/new-langgraph-run
```

当前 LangGraph 版本有13组实际 CLI 记录，见 [examples/langgraph-run/summary.json](examples/langgraph-run/summary.json)。原26项测试全部保留，新增13项图测试，共39项通过，见 [examples/test-results-langgraph.txt](examples/test-results-langgraph.txt)。生成器拒绝覆盖已有证据目录，具体命令、退出码和节点序列写入汇总。

DeepSeek 接入新增7项离线配置/响应测试，当前合计46项通过，见 [test-results-deepseek.txt](examples/test-results-deepseek.txt)。真实API路径另见 [deepseek-run](examples/deepseek-run/summary.json)：两次任务各8次模型调用，最终均为4模拟单位、150分钟，并核对了实际保存文件。

已有真实材料可离线核对，不产生API费用：

```powershell
python -m examples.verify_live --run-dir examples/deepseek-run
```

`examples/offline-run/` 与 `examples/test-results.txt` 保留原生 Python 版本的历史证据，不作为 LangGraph 的运行记录。两版均为离线 Mock；详见 [证据索引](examples/README.md)。

## 评审入口

| 材料 | 内容 |
|---|---|
| [01-requirements](docs/01-requirements.md) | 用户、范围、验收标准 |
| [02-solution](docs/02-solution.md) | 动态决策、硬性校验与技术取舍 |
| [03-evaluation](docs/03-evaluation.md) | 测试与实际结果、证据索引、尚未验证项 |
| [04-ai-usage](docs/04-ai-usage.md) | AI 辅助范围、已发生贡献与待补充项 |
| [05-retrospective](docs/05-retrospective.md) | 工程复盘草稿和后续改进 |
| [06-delivery-check](docs/06-delivery-check.md) | 独立环境复现、提交文件与证据检查、待完成的Git交付 |
| [work-log](docs/work-log.json) | 本轮可观测实现会话的实际计时口径 |

本次为学习显式状态、条件路由与检查点而引入 LangGraph。它接管原生循环，但不替代模型决策或业务校验；不额外引入 LangChain 模型封装、预构建 Agent 或多 Agent。检索仍为关键词匹配，脚本为本地模板，费用/耗时为合成规则；内容质量和真实业务效果尚未评估。

候选人说明全程投入约3小时，并亲自检查关键节点的准确性。AI可观测工作窗口在 work-log 中另行记录，不与个人自报时长相加。源码、测试及文档主体由 AI 辅助生成；完整测试由AI执行，具体个人检查过程和学习总结尚待补充，不虚报个人代码修改或测试执行。

提交前仍需：补充具体个人检查与复盘记录、将材料放入 GitHub 仓库并记录最终 commit SHA。两条真实模型路径已采集。目前没有创建远端或交卷。
