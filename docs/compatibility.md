# 兼容性与验证记录

核对日期：2026-10-28。当前 ASR `0.1.0` 为开发版本，未发布。

## 知识检索 0.1.0

新增 `penhin-plugin-knowledge-retrieval`，需求来自用户提出的“索引项目文档、笔记、转写和媒体理解结果，按问题找回原文并引用来源”。使用与下文相同的 Penhin `690818c9` / `0.1.1` 基线，入口名 `knowledge_retrieval`。服务无第三方运行依赖，插件工厂延迟导入内核。

- 本地实测 Windows x64、Python 3.12.10、SQLite 3.49.1；包要求 Python 3.11+。Ubuntu / 3.11 与 Windows / 3.12 已配置独立 CI，本轮未在本地执行 Linux 或 Python 3.11。
- `python -m pytest plugins/knowledge_retrieval/tests -q`：**42 passed**，其中 34 项服务测试、8 项真实 Penhin / 跨插件集成测试，无跳过。Provider 是确定性替身，无付费模型请求。
- 真实 SQLite 覆盖中英文问题、混合词项、空结果、逐字片段、Unicode / CRLF 定位、持久化重开、来源更新与删除、幂等导入、批量失败回滚、输出预算、项目与来源过滤、授权撤销、非法数据库拒绝，以及取消、重复取消、超时和关闭时的线程回收。
- 真实内核覆盖 entry point 发现、Agent 搜索调用、结构化引用返回、fetch 重新授权、默认工具关闭和共享服务所有权；直接复用当前 `penhin_asr.Transcript` 与 `penhin_media.ContextSupplement` 验证跨插件输入。
- wheel / sdist 构建通过。源码目录之外的干净 Python 3.12 环境安装当前 wheel 后，**34 passed, 1 skipped**；跳过项为未安装 Penhin 的集成模块。独立示例完成四类来源索引、问题检索及原文取回；entry point 与 `pip check` 通过。
- Ruff lint / format、仓库目录与文档链接检查通过。本轮未发布 wheel 的 SHA-256 为 `1c3e7730f833d75e44fd00604ea89b30a4bfc1b6cbbe969657fd8f7774b23123`，不是发行签名。

当前检索是中文双字 / 英文词项的 FTS5 BM25，没有向量语义召回或答案生成。引用定位到导入时快照；ASR 整段时长不会伪装成词级对齐，媒体生成描述与原生提取文字保持区别。大规模质量／性能、目录自动同步、宿主引用 UI、安装授权和端到端回答引用校验尚未验收。完整合同见[知识检索说明](../plugins/knowledge_retrieval/README.md)。

## Penhin 基线

本轮对接 `penhin/penhin` 提交 [`690818c9e2fd201fd87333b0ff92775ae0c59c08`](https://github.com/penhin/penhin/tree/690818c9e2fd201fd87333b0ff92775ae0c59c08)，包版本 `0.1.1`。验证时从干净的该提交本地源码构建并安装，没有修改内核。

使用公开的 `Plugin`、`Tool.from_model`、`ToolContext`、`ToolResult`、`PluginRuntime`、`load_plugin` 和 `penhin.plugins` entry point。旧方案中的 `penhin-plugin.yaml` / `PluginHost` 合同不适用于此内核；历史背景见[调研](research/penhin-plugin-issues-2026-10-28.md)。

服务包不强依赖 Penhin，独立使用无需安装内核；调用插件工厂时才导入 Penhin。当前内核为私有源码仓库，尚未在 PyPI 找到可安装发行，接入者须单独提供匹配内核。基础 CI 不具备该私有仓库访问权限时明确跳过集成模块；本地交付验证必须覆盖真实内核。

## ASR 运行边界

- 后端：sherpa-onnx `2.0.1`、NumPy `2.3.5`；仅 Zipformer 中文流式 int8 `2025-06-30`，文件摘要见[模型锁](../plugins/asr/src/penhin_asr/model-lock.json)。
- 实测环境：Windows x64、Python 3.12、CPU。Linux 的无模型合同测试纳入 CI，尚未在本次本地环境执行；不宣称其他平台的真实模型识别已通过。
- 输入：整段为单声道 PCM16 WAV；流式为异步 PCM16 字节块（每块最多 1 秒），8–48 kHz，默认累计 10 MiB / 120 秒上限。浏览器录音格式转换、录音手势和编辑器属于宿主。
- 整段与流式共用 OnlineRecognizer；流式输出可修订的全文快照，输入 EOF 后返回唯一最终结果。没有自动静音切句，不承诺该中文模型的英文或中英混合识别质量。
- 直接语音交付通过 `prepare_voice()` 返回分离的模型文字与语音展示数据；也可用 `VoiceSubmission.from_transcript()` 复用最终流式结果。是否显示文字由宿主选择，不改变 ASR 模型。默认插件可仅注册生命周期，传入工具 reader 时才暴露 `asr__transcribe`。
- 每次录音独立工作进程，在会话内复用模型与解码状态；跨会话仍有冷启动。默认超时 60 秒，整段限制总执行时间，流式限制等待下一条解码结果（含等待输入和首次加载）的时间。取消或关闭后终止并等待进程退出；不提供 OS 安全隔离。

## 实际验证

验证覆盖 WAV 与 PCM 格式/累计大小/时长限制、并发拒绝、取消与重复取消、超时、迟到结果、共享资源所有权、工作进程创建期间取消、进程回收、输出上限、错误脱敏、流式背压、提前退出和输入结束前输出。语音提交覆盖展示数据不含文字、附件 ID 绑定、读取阶段的超时/取消/关闭、空识别阻断、复用最终结果、准备失败不调用模型且不追加 Session query。集成使用真实 Penhin 工厂、入口发现与 Agent 执行路径；模型 Provider 使用确定性替身，不调用付费 API。

真实模型使用上游压缩包中的 `0.wav`、`1.wav`、`8k.wav`，样本摘要固定在模型锁。`0.wav` 的结果与[官方示例](https://k2-fsa.github.io/sherpa/onnx/pretrained_models/online-transducer/zipformer-transducer-models.html#sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30-chinese)公布的文本一致：“对我做了介绍啊那么我想说的是呢大家如果对我的研究感兴趣呢”。这验证推理接入一致性，不是独立人工标注的准确率评测。

三段样本分别用 20 / 320 / 100 ms 音频块处理，并在输入源 EOF 前等待首个非空结果：如果后端先缓存整个输入才识别，该测试会超时。三段流式最终文本均与整段调用一致，覆盖 8 kHz 输入。这里没有实测麦克风链路延迟；生产输入仍应允许用户编辑后提交。

本轮最终验证：

- `python -m pytest plugins/asr/tests -q`：设置真实模型目录后 **81 passed**，其中 66 项基础/语音交付/流式/进程/资产测试、11 项 Penhin 集成、4 项真实模型测试；无跳过。
- 真实内核验证语音转写以 `user` 内容提交，系统提示保持原样，下一轮保留内部文字且不重复识别；展示数据只包含语音引用及时长。本轮只修改插件仓库，未修改 Penhin 内核或 penhin-code。
- ASR Ruff lint / format 与 `git diff --check` 通过。仅包含本次 ASR 改动的提交快照已通过目录及本地文档链接检查；工作区并行开发的 TTS 改动不在本次提交和验证范围内。
- wheel 与 sdist 构建通过；sdist 包含测试夹具和依赖锁。干净 Python 3.12 环境通过 `--require-hashes` 安装后端依赖，再安装 wheel，`pip check` 通过。
- 在仓库之外、未安装 Penhin 的独立环境中重新安装当前 wheel，真实中文 `prepare_voice()` 通过：5.6115 秒样本产生 28 字内部文字，展示数据仅含 audio 类型、来源和时长，证明提交准备服务可独立使用。
- wheel 与 sdist 均包含当前提交接口与测试，未恢复旧后端。本轮 wheel SHA-256 为 `9f445b791303aaca17dcd36d04880ab2244f6d529141ddf1fc43f50399b3aab1`；这是未发布源码的本地构建摘要，不是已发布签名。

模型压缩包未包含权重 LICENSE，其 README 指向的上游模型卡本次无法访问（401），权重许可尚未核实。源码包与 wheel 不含权重；来源与当前许可状态保存在模型锁，不能从代码许可推断权重许可。

## 仍需宿主完成的 #42 范围

[penhin-code#42](https://github.com/penhin/penhin-code/issues/42) 仍要求真实按住录音、Attachment 生命周期、输入框编辑提交、安装验证与授权等宿主流程。当前 ASR 包提供可调用能力与清理合同，不能据此关闭完整交付任务。安装验证不由本包替代，`pip install` 也不等于完成宿主的授权流程。

## 媒体理解 0.1.0

同样对接上述 Penhin `690818c9` / `0.1.1`，独立包为 `penhin-plugin-media-understanding`，入口名 `media_understanding`，使用 `Plugin`、`PluginRuntime`、可选工具和宿主请求投影。需求仍来自 [penhin-code#41](https://github.com/penhin/penhin-code/issues/41)，实现不依赖旧版 broker 协议。

- 实测环境：Windows x64、Python 3.12。包要求 Python 3.12+，与 PyAV 19 要求一致；Linux / Python 3.12 已配置独立 CI，本轮未在本地执行 Linux。
- 本地解码：Pillow `13.0.0`、pypdfium2 `5.14.0`、PyAV `19.0.2`；可选本机 Ollama HTTP 接口使用 HTTPX `0.28.1`。来源、许可及完整依赖锁见[媒体插件说明](../plugins/media_understanding/README.md)。
- 源码环境和全新 wheel 环境均 **53 passed, 1 skipped**。覆盖真实 PNG、PDF 原生文字/页序、MP4/WebM 采样帧、损坏/超限输入、来源匹配、首次启用回调、整批失败、取消/重复取消/超时、启动中取消、子进程回收、HTTP 错误脱敏和响应上限、真实内核 entry point/Agent/停用清理。
- 独立环境安装 `requirements-local.lock` 时启用 `--require-hashes`；wheel 在仓库之外运行测试，`pip check` 无冲突。wheel/sdist 构建、Ruff lint/format、仓库目录/文档检查通过，sdist 含测试 fixtures 和依赖锁。
- 本次 wheel SHA-256：`4e58df840e91e01de23a18bfd16aef868d6859067e14f5df1a17ef0e9fa0b830`。这是当前未提交源码构建的本地校验值，非签名发行。
- 未配置真实视觉模型，因此跳过真实模型 smoke；HTTP 合同使用替身，不代表语义识别质量已经验证。模型权重及 Ollama 守护进程由宿主选择并管理。

插件提供 `prepare_fallback` 的启用 gate 和失败传播；持久化项目启用 UI、安装验证/授权、附件存储、原始媒体直传与受限重试仍须宿主接入。不能以插件单测和 wheel 安装通过为由关闭 #41。

## TTS 0.1.0

2026-10-28 新增，需求来自用户直接提出的中文 TTS、完整/流式文本、分段播放、打断与 ASR 组合。独立包 `penhin-plugin-tts`，Penhin 入口 `tts`，对接同一真实内核基线；未修改内核和 ASR 的接口。

- 后端：sherpa-onnx `2.0.1` / NumPy `2.3.5`，固定 `vits-melo-tts-zh_en`（单音色、44.1 kHz、CPU）。上游包含 MyShell.ai MIT LICENSE；推理文件、中文规则与词典摘要见[模型锁](../plugins/tts/src/penhin_tts/model-lock.json)。权重不打包。
- 实测：Windows x64 / Python 3.12。包支持 Python 3.11+；Linux 3.11 / Windows 3.12 合同测试已配置 CI，尚未在本机验证 Linux 声卡或真实模型。
- 完整文本与异步增量文本共用分段推理，支持标点/最长长度/等待时间切段、PCM16 输出、WAV 汇总。不是模型内部音频帧级双流式；每轮冷启动，未提供跨轮预热或延迟承诺。
- `SpeechPlayer` 顺序播放并协调设备 stop、清空队列和进程终止；并发打断共用一次清理。`VoiceInteraction` 串接真实 ASR 最终文本、宿主回答流、TTS，取消时关闭回答生成器。宿主仍负责麦克风、VAD、回声消除和用户提交交互。
- 设置 TTS 和 ASR 模型目录后 **64 passed，无跳过**：包含 6 项真实 Penhin 集成、3 项真实模型/组合测试；其余覆盖切段、背压、输入与音频上限、空输入、取消/重复取消/并发打断、迟到音频丢弃、模型加载超时、子进程回收、资产校验、播放器回调和错误脱敏。
- 真实合成 smoke 生成 3.621 秒中文音频，回送 Zipformer 得到“你好欢迎使用中文语音今天是十月二十八日”；这是链路一致性检查，不是主观音质、口音、多音字或真实对话延迟评测。流式模型测试在文本 EOF 前等待第一段，另以真实 ASR 样本驱动完整回答合成链路。
- 干净 Python 3.12 环境通过 `--require-hashes` 安装合成/播放依赖和 wheel，`pip check` 通过。在仓库外、未安装 Penhin 的环境中，入口发现、完整文本 CLI 和标准输入流式 CLI 均通过，分别生成 2.034 秒和 3.129 秒非空 44.1 kHz WAV。
- Ruff、wheel/sdist 构建、仓库目录/文档检查通过。ASR 回归 **81 passed**；媒体理解回归 **53 passed, 1 skipped**（未配置真实视觉模型）。未改动这些插件的实现。
- 已使用本机默认 PortAudio 输出设备执行完整播放与播放中打断，修正并验证了 C `void` 结束回调的返回合同。修正后 wheel 的一次打断清理耗时约 12 ms（Python 调用到资源回收），不代表实测声学停声延迟；未进行人耳音质评审、麦克风或回声消除验收。
