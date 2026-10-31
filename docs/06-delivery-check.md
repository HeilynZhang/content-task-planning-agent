# 提交前交付检查

检查日期：2026-10-31。结论：本地安装、运行与证据复核通过；尚未建立Git仓库，最终commit SHA与GitHub访问权限待上传时核对。本次未增加业务功能或重新调用付费模型API。

| 检查项 | 实际结果 |
|---|---|
| 干净环境安装与运行 | 新建Python 3.11隔离环境，禁用系统site-packages；按requirements.txt安装依赖，在不含.env和缓存的源码快照中执行 |
| 全部测试 | 46项通过，见[clean-tests.log](../examples/submission-check-20261031/clean-tests.log) |
| README离线命令 | 成功生成并实际保存方案，输出与保存文件一致 |
| 核心场景复现 | 重新通过13组CLI场景，包括缺信息、预算调整、工具/保存失败、幂等、时间约束及两项执行上限 |
| 真实模型证据 | 在快照中离线核对现有两条DeepSeek Trace及实际保存结果；新增LLM API调用0次 |
| 提交内容 | 拟提交源码、合成数据与证据中没有匹配本地已知API Key；示例密钥为占位值；快照未包含.env、缓存或编辑器配置 |
| 链接 | 原有47个Markdown链接的本地目标均存在，6个外部官方文档链接可打开；本报告新增链接另行核对 |
| 时间与AI使用 | 已记录候选人自报约3小时、亲自检查关键节点；AI编写与执行范围单独说明 |
| Git提交与远端 | 当前不是Git仓库，无commit SHA或远端；Git暂存区、仓库可见性及评审访问权限尚不能确认 |

完整自动检查结果见[summary.json](../examples/submission-check-20261031/summary.json)。新环境实际依赖版本见[installed-packages.log](../examples/submission-check-20261031/installed-packages.log)。其中LangGraph为1.2.9、checkpoint为4.1.1，传递依赖由本次安装实际解析，不假定与原开发环境完全相同。

## 检查中修正的问题

1. 旧版Windows pip按GBK读取依赖文件，原中文注释导致UnicodeDecodeError。已将该注释改为ASCII，依赖版本不变。
2. 首次沙箱联网下载超时；获得联网执行许可后重试，依赖安装成功。该失败属于安装环境，未写成代码测试失败或已完成结果。
3. 将本地`.vscode/`加入忽略规则。原`.env`、`.venv/`、`__pycache__/`及运行态数据排除规则保留。

## 证据边界

本次检查在本机Windows上的独立环境和源码快照中执行，未声称验证其他操作系统。源文件清单与SHA256记录在[source-manifest.json](../examples/submission-check-20261031/source-manifest.json)，对应被测快照，不是Git commit SHA。检查完成后补充的交付说明文档不改变业务代码。

密钥检查覆盖拟提交文件，尚无实际Git暂存区可核对；建仓时仍应检查暂存文件列表。个人贡献按候选人说明记录，不将AI运行的46项测试表述为候选人亲自执行。

下一步建立GitHub仓库、上传经检查的文件，记录最终commit SHA，并确认评审可访问后再交卷。
