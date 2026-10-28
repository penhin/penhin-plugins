# Penhin 插件需求调研

核对日期：2026-10-28（Asia/Shanghai）。本页先记录 GitHub issue 的需求与状态，再给出面向 `penhin/penhin` 重做插件库的建议；issue 中的旧宿主协议不代表当前 Penhin 已提供同名接口。

## 调研范围与结论

- 对 `penhin/penhin` 执行全状态 issue 查询，结果为空；仓库 API 显示已启用 issues，`open_issues_count` 为 0。因此当前无法从这个仓库提取已登记的插件需求。[Penhin issues](https://github.com/penhin/penhin/issues)、[仓库 API](https://api.github.com/repos/penhin/penhin)
- 对 `penhin/penhin-code` 检索全部 issue，并阅读插件需求、总规格、前置任务和旧任务的关闭评论。明确待交付的官方插件是 **ASR** 与 **media-understanding**，两项均为 OPEN、`ready-for-agent`，最近更新于 2026-10-22。[ASR #42](https://github.com/penhin/penhin-code/issues/42)、[媒体理解 #41](https://github.com/penhin/penhin-code/issues/41)
- `penhin/penhin-plugins` 的全状态 issue 查询也为空；当前具体产品验收条件来自 `penhin-code`，不能写成 `penhin/penhin` 已批准的新接口。[插件仓库 issues](https://github.com/penhin/penhin-plugins/issues)

可重复查询：

```sh
gh issue list --repo penhin/penhin --state all --limit 200 --json number,title,state,url
gh api repos/penhin/penhin --jq '{has_issues,open_issues_count}'
gh issue list --repo penhin/penhin-code --state all --limit 200 --json number,title,state,updatedAt,url
gh issue view 41 --repo penhin/penhin-code --json body,comments,state,labels,url
gh issue view 42 --repo penhin/penhin-code --json body,comments,state,labels,url
```

## 明确希望实现的插件

| 候选 | 当前来源与状态 | 用户得到的能力 | 范围与约束 |
| --- | --- | --- | --- |
| `asr` | [#42](https://github.com/penhin/penhin-code/issues/42)，OPEN、ready-for-agent | 按住说话，松开后转写；最终文字回填输入框，用户可编辑后再提交 | 独立官方插件；明确手势才录音；partial 仅展示；不能自动发送模型请求 |
| `media_understanding`（建议的仓库 ID） | [#41](https://github.com/penhin/penhin-code/issues/41)，OPEN、ready-for-agent | 为不支持原始媒体的模型提供图片、PDF、视频帧的本地理解结果 | 输出有顺序、带来源标记的不可信上下文补充；首次回退须启用项目配置，之后按需启动；处理失败阻止整次含媒体提交 |

上述两个 issue 没有给出彼此之间的优先级、具体引擎、模型名称或首发操作系统。`media_understanding` 是本报告为适配现有仓库 ID 约定提出的名称，不是 issue 已规定的 Python API 或包名。[#41](https://github.com/penhin/penhin-code/issues/41)、[#42](https://github.com/penhin/penhin-code/issues/42)

### ASR 的验收边界

1. 一次明确的按住说话动作创建一次音频 Attachment；松开或取消结束录音。
2. 经验证、授权的本地 ASR 插件执行转写。中间结果仅供 UI 展示，最终结果进入可编辑输入框；没有用户提交动作就不调用模型。
3. 禁用、reload、取消和会话退出均停止相关录音与转写，其他插件仍可使用。
4. 保留安装验证、授权、处理上限、日志脱敏和普通资源清理。音频和转写文本始终是不可信输入。

以上均来自当前 [#42](https://github.com/penhin/penhin-code/issues/42)。视频中需要识别的音轨应委托独立 ASR，媒体理解与 ASR 不合并成一个插件，见总规格 [#32](https://github.com/penhin/penhin-code/issues/32)。

### 媒体理解的验收边界

1. 覆盖图片、PDF 和视频帧；通过宿主管理的 Attachment 访问合同读取输入。
2. 结果带插件/输入来源与顺序，作为不可信上下文补充；不能升级为系统提示词，也不能把原始媒体写入普通日志。
3. 模型支持原始媒体时，由核心直接编码并发送；模型不支持时才进入本地理解回退。
4. 第一次回退使用需显式启用项目配置；已安装、已授权且已启用后可按需启动。处理失败不能悄悄丢弃媒体继续回答。
5. 仅当 Provider 在生成文本、工具调用或其他副作用前明确拒绝媒体时，核心可调用回退并重试一次；普通网络错误、其他 Provider 错误及生成后失败不允许借此自动重试。

插件处理职责来自 [#41](https://github.com/penhin/penhin-code/issues/41)；直传、回退和重试规则来自 [#38](https://github.com/penhin/penhin-code/issues/38) 与 [#32](https://github.com/penhin/penhin-code/issues/32)，属于宿主集成要求，不能全塞入媒体插件。

## 历史愿望与已被替代的任务

| 历史内容 | 当前解释 | 证据 |
| --- | --- | --- |
| 单独官方 OCR 插件 #24 | CLOSED 是被 #41 替代；OCR 扩展进媒体理解，不能据此认为已经交付，也不应默认再建第三个独立官方 OCR 插件 | [#24 关闭评论](https://github.com/penhin/penhin-code/issues/24#issuecomment-5648405039) |
| 旧 ASR 交付 #25 | CLOSED 是被 #42 替代；本仓库原先围绕 #25 的说明需要迁移到当前需求 | [#25 关闭评论](https://github.com/penhin/penhin-code/issues/25#issuecomment-5648405227) |
| 旧输入增强总规格 #19 | 被 #34、#38、#41 与 #32 的新规格替代；其“视觉模型默认仍运行 OCR”等旧策略不能当成当前策略 | [#19 关闭评论](https://github.com/penhin/penhin-code/issues/19#issuecomment-5648404843)、[#32](https://github.com/penhin/penhin-code/issues/32) |
| PDF 提取、图片描述、图片理解 | #19 曾列为扩展场景；当前正式媒体理解任务已覆盖对应媒体类别，但尚未规定内部引擎或拆分模块 | [#19](https://github.com/penhin/penhin-code/issues/19)、[#41](https://github.com/penhin/penhin-code/issues/41) |
| 条码 / QR 识别 | #19 的可组合扩展示例，没有查到独立交付 issue 或当前验收标准；列为后续候选 | [#19](https://github.com/penhin/penhin-code/issues/19) |
| 部署、数据访问、团队专用 AI 工作流 | 插件平台 #3 的动机场景，没有查到独立官方插件交付范围；该平台任务还明确将具体领域插件排除在当时范围外 | [#3](https://github.com/penhin/penhin-code/issues/3) |

本次全量 issue 检索未发现 TTS 的明确需求。上述“未发现”仅限本次可见的 issue 正文与评论，不表示其他仓库、讨论或未记录需求中不存在。

## 前置工作与职责归属

| 工作 | issue 当前状态 | 应由何处承担 |
| --- | --- | --- |
| Attachment 上限、用户目录持久化、项目身份隔离、凭据库密钥、七天过期、脱敏记录 | [#34](https://github.com/penhin/penhin-code/issues/34) OPEN，无前置；#41 与 #42 均依赖它 | 宿主应用/核心管理媒体生命周期；插件消费输入 |
| Provider/model 媒体能力注册、原始媒体编码、回退决策、单次受限重试 | [#38](https://github.com/penhin/penhin-code/issues/38) OPEN，依赖 #34；#41 依赖它 | 核心及 Provider 适配层；插件提供本地理解结果 |
| 按计划选择第三方插件、按运行激活 namespaced Contributions | [#37](https://github.com/penhin/penhin-code/issues/37) OPEN，依赖 #33 | `penhin-code` 的规划与插件组合层；不是一个领域插件 |
| 录音手势、输入框回填、用户提交、会话退出 | [#42](https://github.com/penhin/penhin-code/issues/42) 与 [#32](https://github.com/penhin/penhin-code/issues/32) 规定交互结果 | 新接口设计应由宿主控制交互，ASR 提供转写服务；具体接入当前 Penhin 的方式需要源码核对 |
| Artifact 验证、授权、激活、调用生命周期 | [#32](https://github.com/penhin/penhin-code/issues/32) 仍保留 | `penhin-code` 现有产品治理层；迁移到轻量 Penhin 时要重新明确哪些由应用承担 |

依赖关系是 `penhin-code` 现有需求的产品验收关系，不意味着开始实现插件算法前必须等待这些 issue 全部关闭。独立服务、可注入后端、合同测试可先实现；端到端交付则不能跳过宿主集成。这是本报告的实施建议。

### 2026-10-22 的执行范围修订

当前总规格已经把插件执行范围改为**用户选择并信任的本地代码**。OS Sandbox 是延期项，不阻塞 ASR 或媒体理解；验签证明来源/完整性，broker 约束经 broker 发起的请求，两者都不限制插件直接调用主机 OS。子进程和 venv 也不等于隔离。[#32](https://github.com/penhin/penhin-code/issues/32)

- #41 当前只保留 #34、#38 为前置；#42 当前只保留 #34。[#41](https://github.com/penhin/penhin-code/issues/41)、[#42](https://github.com/penhin/penhin-code/issues/42)
- Linux/WSL2、Windows 隔离、资源配额和隔离工作区变更仍为 OPEN、deferred 的未来工作，不能称为已完成或拿来阻塞当前可信插件交付。[#35](https://github.com/penhin/penhin-code/issues/35)、[#39](https://github.com/penhin/penhin-code/issues/39)、[#40](https://github.com/penhin/penhin-code/issues/40)、[#60](https://github.com/penhin/penhin-code/issues/60)、[#61](https://github.com/penhin/penhin-code/issues/61)、[#62](https://github.com/penhin/penhin-code/issues/62)
- 普通取消、超时、输出上限、脱敏、授权和正常生命周期清理仍然需要实现；不承诺恶意后代进程或核心被强制终止后的强隔离清理。[#32](https://github.com/penhin/penhin-code/issues/32)、[#42](https://github.com/penhin/penhin-code/issues/42)

## 对重做 `penhin-plugins` 的建议

以下是依据需求作出的设计建议，尚不是上游已批准的 API：

1. **先对齐当前 `penhin` 的真实公开接口。** 将 `penhin-code` issue 作为使用场景与验收来源，把领域能力做成可复用 Python 服务，再提供薄的 Penhin Plugin 适配层；不要直接复制旧 `PluginHost`、manifest 和 broker 协议并声称已兼容新内核。
2. **ASR 先做最小闭环。** 它的范围更集中、上游列出的前置更少，适合先验证加载、调用、取消和资源释放；这是建议顺序，不是 issue 给定优先级。按住说话是提交前的宿主动作，应直接调用转写服务，不能等待模型选择一个 tool 才开始录音/转写。
3. **媒体理解作为第二个独立插件。** 内部可按图片/OCR、PDF、视频帧拆解后端，但对外统一结果结构；需要转写视频音轨时复用 ASR。宿主保留原始媒体直传、项目启用和失败是否阻止提交的决策。
4. **先冻结最小合同，再选引擎。** 明确媒体输入、来源信息、最终/中间结果、取消、错误和资源生命周期；依赖/模型由应用配置并固定。Issue 没有选定识别引擎，不能把某个模型或平台的支持当成既定要求。
5. **把额外候选留在 backlog。** QR/条码、部署、数据访问和团队工作流暂不进入首轮实现；没有独立验收范围时，先保留扩展方式即可。

## 当前 Penhin 的公开接口基线

本节核对 `penhin/penhin` 远程 `main` 提交 [`8a9e680d1a026efe028e8a423bcf41c3c9c82004`](https://github.com/penhin/penhin/tree/8a9e680d1a026efe028e8a423bcf41c3c9c82004)，与本地 `../penhin` 的 HEAD 一致。本地工作区还存在未提交改动（包括插件取消与清理实现），因此以下接口事实以该提交为准；未把工作区修改当成已发布能力。本次没有改动内核仓库，也没有运行跨仓库兼容性测试。

| 接入面 | 已有接口 | 对本仓库的含义 |
| --- | --- | --- |
| 插件对象 | `Plugin(name, version, tools=(), transform_context=None, observe=None, startup=None, shutdown=None)` | 工厂应返回内核的 `Plugin`，复用内核类型 |
| 显式加载 | `load_plugin("module:factory", **host_dependencies)` 或已安装的 `penhin.plugins` entry point | 后端、配置与宿主服务通过工厂参数注入；包可声明 `[project.entry-points."penhin.plugins"]` |
| 发现与激活 | `discover_plugins()` 只列出入口名；`await runtime.activate(plugin)` 才加入能力目录 | 安装、发现、加载与启用是不同阶段；不自动扫描并激活代码 |
| Agent 调用 | `Agent(model, plugins=runtime)`；工具命名为 `plugin__tool` | 可用 `@tool` 或 `Tool.from_model(...)` 包装服务；参数 Schema 与本地校验来自同一模型 |
| 上下文与事件 | `transform_context(Context)`、`observe(event)` | 前者在模型请求前变换投影；后者观察 Agent 事件。两者都不是录音输入框 API |
| 生命周期 | `startup`、`shutdown`，以及 `deactivate`、`close`、`async with PluginRuntime()` | 运行中改变插件会被 `PluginBusy` 拒绝；插件须明确资源所有权与清理方式 |

接口依据：[插件定义与加载器](https://github.com/penhin/penhin/blob/8a9e680d1a026efe028e8a423bcf41c3c9c82004/src/plugins.py)、[工具合同](https://github.com/penhin/penhin/blob/8a9e680d1a026efe028e8a423bcf41c3c9c82004/src/tools.py)、[Agent 接入](https://github.com/penhin/penhin/blob/8a9e680d1a026efe028e8a423bcf41c3c9c82004/src/agent.py)、[该提交的使用说明](https://github.com/penhin/penhin/blob/8a9e680d1a026efe028e8a423bcf41c3c9c82004/README.md#plugin)。

当前内核把插件作为可信的进程内 Python 代码，安装交给 pip，不提供旧版独立安装器、子进程 Host 或 OS 沙箱。工具支持注入 `ToolContext`（含调用身份和取消信号），结果须能转换为 JSON；同步阻塞推理不能靠取消协程强行终止，需要后端支持合作取消或配置超时。这些是当前接口的实际边界。[源码 README](https://github.com/penhin/penhin/blob/8a9e680d1a026efe028e8a423bcf41c3c9c82004/README.md)、[工具合同](https://github.com/penhin/penhin/blob/8a9e680d1a026efe028e8a423bcf41c3c9c82004/src/tools.py)

因此，旧文档中的 `penhin-plugin.yaml`、`PluginHost.invoke`、`AttachmentBroker`、`api_version: 1` 等不能当作这套内核的既有公开接口。另一方面，#41/#42 仍要求 `penhin-code` 完成 Artifact 验证和授权；使用 Python 包本身不会自动满足这些产品要求，应由具体宿主的集成层承接。[当前内核加载器](https://github.com/penhin/penhin/blob/8a9e680d1a026efe028e8a423bcf41c3c9c82004/src/plugins.py)、[#41](https://github.com/penhin/penhin-code/issues/41)、[#42](https://github.com/penhin/penhin-code/issues/42)

### 建议的调用分层

以下为设计建议，方法名、输入模型和包名尚未冻结：

```text
按住说话：宿主录音 → ASR 服务 → 最终文本回填编辑框 → 用户提交 → Agent.run
工具调用：Agent → Penhin Plugin 工具适配 → 同一个能力服务 → 结构化结果
媒体输入：宿主检查模型能力 → 原始媒体直传，或媒体理解服务 → 带来源的内容 → 请求投影
```

- **能力服务**提供转写或媒体理解，声明输入、最终结果、可选进度、错误、取消和关闭合同。它不持有编辑器，也不决定何时发送模型请求。
- **Penhin 适配层**提供 `create_plugin(...) -> Plugin`，按场景贡献工具或上下文变换。按住说话直接由宿主调用服务；可选的模型工具共用实现，不能反过来要求用户先发起一次模型调用才能转写。
- **宿主集成层**提供录音、Attachment 访问、配置启用、验证授权、原始媒体路由和 UI 回填。上下文变换应消费已准备的结果，避免每轮模型请求都重复识别同一媒体；媒体内容保持不可信数据身份。
- **依赖所有权**应显式约定。宿主注入的共享后端由宿主关闭；插件自建的资源由其生命周期管理，避免停用 ASR 时影响其他插件。

这些分层结合了当前 Plugin 的依赖注入方式和 #41/#42 的产品流程，尚未实现，也不等于已经通过旧宿主的端到端验收。

## 首轮实施切片与验证建议

1. **迁移仓库基础约定。** 按当前 Penhin 的工厂与 entry point 方式建立 Python 包、插件目录与最小调用示例；调整现有目录检查和发行文档，清楚区分内核 Python 包接入与 `penhin-code` 的安装治理。不能仅替换 README 标题就称为兼容。
2. **完成 ASR 服务与适配。** 先固定音频输入、final/partial、取消、错误和资源释放合同，再选择真实引擎、模型、支持平台与依赖版本。验证宿主能直接转写，以及 Plugin 能通过 `load_plugin`、`PluginRuntime` 和 Agent 工具路径复用服务。
3. **接通真实宿主。** 完成录音、编辑、提交，以及取消、禁用和退出处理；用真实音频验证识别，用受控模型替身断言“用户提交前模型请求数为零”。模拟后端通过只能证明接入合同，不能证明识别质量或完整交付。
4. **实现媒体理解。** 先接通图片/OCR，再扩展 PDF 与视频帧；统一来源、顺序和诊断结果，并验证模型原生支持、回退启用、处理失败等宿主分支。内部实现可逐步交付，但最终覆盖范围仍按 #41 验收。

这些切片是本次调研的建议，不是已有实现。引擎、模型许可、运行平台、输入上限和效果指标仍需在各插件实现规格中明确。

## 本次变更与检查范围

- 本次新增此调研记录，并在根 README 增加迁移提示与入口；未新增插件实现或发布制品。
- 调研开始前，本地已有 `plugins/asr/README.md` 的删除，原样保留。
- `python scripts/check_repository.py` 的既有基线失败共四项：ASR README 缺失，以及 README / 发行文档中的三处失效链接。该失败源于原有删除，不代表本次已经修复目录或完成新接口迁移。
- 新增文档的本地链接与本次修改的空白格式单独核对；没有运行识别模型、真实录音或跨仓库端到端测试。
