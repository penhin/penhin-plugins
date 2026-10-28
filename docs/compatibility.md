# 核心兼容性与集成前置工作

本页记录初始化时对 `penhin/penhin-code` 的源码核对，不是已通过的兼容性认证。

- 核对日期：2026-09-12。
- 核心提交：[`f6e609ae19d6e14488063a6bc3d6354c10558e01`](https://github.com/penhin/penhin-code/tree/f6e609ae19d6e14488063a6bc3d6354c10558e01)。
- 接口基线：Plugin manifest `api_version: 1`；宿主 `PROTOCOL_VERSION = 1`。
- [#21](https://github.com/penhin/penhin-code/issues/21)、[#22](https://github.com/penhin/penhin-code/issues/22)、[#23](https://github.com/penhin/penhin-code/issues/23) 已关闭；[#25](https://github.com/penhin/penhin-code/issues/25) 仍未完成。

## 可沿用的合同

[`penhin/plugins/host.py`](https://github.com/penhin/penhin-code/blob/f6e609ae19d6e14488063a6bc3d6354c10558e01/penhin/plugins/host.py) 已定义 `Invocation`、`AttachmentDescriptor`、`AttachmentBroker`、`HostProgress`、`HostDiagnostic`、`HostResult`，以及 `PluginHost.invoke/cancel/close`。

元数据通过 JSON 兼容结构传递。音频内容由 broker 按 invocation 和 attachment 身份读取，不在 JSON 中嵌入大段音频，也不传递宿主文件路径。插件不得绕过 Attachment Session 的失效和取消语义。

## ASR 交付还需要接通的部分

| 当前源码事实 | 后续工作 |
| --- | --- |
| `service.py` 的生产加载入口目前构建工具型 `LocalPlugin`；其子进程通信尚未接通 `host.py` 的输入宿主合同 | 在核心实现可安装 ASR 的加载与调用通路，验证真实运行路径 |
| `load_local_plugin()` 创建空的临时 venv，尚未恢复锁定依赖 | 实现并验证从已验证 Artifact 准备依赖、推理运行时和模型资产的流程 |
| `HostedInputEnricher` 当前执行 `input.enrich` 并返回上下文补充；没有 ASR 最终文本写入编辑器的专用通路 | 核心明确转写操作、结果 schema 和编辑器回填接口；部分结果仅展示，最终结果等待用户编辑和提交 |
| `PythonSdkHost` 在进程内调用 handler；`cancel()` 记录 ID，但不能中断已进入的 handler | 接通实际隔离宿主，验证识别中的取消、超时与进程回收，不能以 SDK 模拟调用证明这些能力 |
| 核心仓库尚无 #25 的按住录音完整实现 | 核心实现录音、松开、取消、禁用、reload 和会话退出时的生命周期，并拒绝迟到结果 |
| Git source 解析会克隆整个仓库；没有子目录选择或 ZIP 解包分支 | 每个插件构建独立 Artifact。当前可行入口是下载并解压后传入本地 Artifact 目录；远程发行 URL 的直接安装需核心支持 |

加载入口见 [`service.py`](https://github.com/penhin/penhin-code/blob/f6e609ae19d6e14488063a6bc3d6354c10558e01/penhin/plugins/service.py)。安装源与完整性检查见 [`installation.py`](https://github.com/penhin/penhin-code/blob/f6e609ae19d6e14488063a6bc3d6354c10558e01/penhin/plugins/installation.py)。

音频合同还须遵循当前每 Attachment 10 MiB 上限；Input Enricher 的 MIME 筛选目前支持精确匹配或 `*`，不能假定 `audio/*` 可用。相关实现见 [`input.py`](https://github.com/penhin/penhin-code/blob/f6e609ae19d6e14488063a6bc3d6354c10558e01/penhin/plugins/input.py) 和 [`runtime.py`](https://github.com/penhin/penhin-code/blob/f6e609ae19d6e14488063a6bc3d6354c10558e01/penhin/plugins/runtime.py)。

## 发行签名的前置修正

当前 `verify_plugin_artifact()` 使用 HMAC-SHA256，官方信任根在源码中公开固定。由于 HMAC 的签名和验证使用同一密钥，这个实现不能证明 Artifact 来自官方发布者。

首个正式发行前，需要核心与本仓库共同采用非对称签名验证或等价的可信发行机制，并确定发布者身份、验证公钥和轮换规则。签名私钥只用于受控发行环境。本仓库不生成依赖公开固定 HMAC 密钥的“官方签名”。

当前验证器还要求 `dependencies` 和 `model_assets` 都是非空的文件路径到摘要的映射。ASR 必须锁定真实使用的依赖和模型；今后无模型插件的表示方式应由核心合同明确，不能靠伪造占位模型通过检查。

## 兼容性证据

每次插件发行都应记录实际验证过的核心版本及提交、manifest / host / result schema 版本、操作系统与架构、引擎和模型版本，以及跨仓库端到端测试结果。初始化阶段的接口版本不等于对所有核心版本的兼容承诺。

## 媒体理解 0.1.0

对接 Penhin 提交 [`690818c9e2fd201fd87333b0ff92775ae0c59c08`](https://github.com/penhin/penhin/tree/690818c9e2fd201fd87333b0ff92775ae0c59c08)，包版本 `0.1.1`，独立包为 `penhin-plugin-media-understanding`，入口名 `media_understanding`，使用 `Plugin`、`PluginRuntime`、可选工具和宿主请求投影。需求仍来自 [penhin-code#41](https://github.com/penhin/penhin-code/issues/41)，实现不依赖旧版 broker 协议。

- 实测环境：Windows x64、Python 3.12。包要求 Python 3.12+，与 PyAV 19 要求一致；Linux / Python 3.12 已配置独立 CI，本轮未在本地执行 Linux。
- 本地解码：Pillow `13.0.0`、pypdfium2 `5.14.0`、PyAV `19.0.2`；可选本机 Ollama HTTP 接口使用 HTTPX `0.28.1`。来源、许可及完整依赖锁见[媒体插件说明](../plugins/media_understanding/README.md)。
- 源码环境和全新 wheel 环境均 **53 passed, 1 skipped**。覆盖真实 PNG、PDF 原生文字/页序、MP4/WebM 采样帧、损坏/超限输入、来源匹配、首次启用回调、整批失败、取消/重复取消/超时、启动中取消、子进程回收、HTTP 错误脱敏和响应上限、真实内核 entry point/Agent/停用清理。
- 独立环境安装 `requirements-local.lock` 时启用 `--require-hashes`；wheel 在仓库之外运行测试，`pip check` 无冲突。wheel/sdist 构建、Ruff lint/format、仓库目录/文档检查通过，sdist 含测试 fixtures 和依赖锁。
- 本次 wheel SHA-256：`4e58df840e91e01de23a18bfd16aef868d6859067e14f5df1a17ef0e9fa0b830`。这是当前未提交源码构建的本地校验值，非签名发行。
- 未配置真实视觉模型，因此跳过真实模型 smoke；HTTP 合同使用替身，不代表语义识别质量已经验证。模型权重及 Ollama 守护进程由宿主选择并管理。

插件提供 `prepare_fallback` 的启用 gate 和失败传播；持久化项目启用 UI、安装验证/授权、附件存储、原始媒体直传与受限重试仍须宿主接入。不能以插件单测和 wheel 安装通过为由关闭 #41。
