# Penhin Code Plugins

为 [Penhin Code](https://github.com/penhin/penhin-code) 交付官方 Plugin 的独立仓库。多个插件共享维护和构建约定，每个插件独立版本、独立构建、独立签名和发行。

当前仓库处于初始化阶段，尚无可安装的官方 Plugin Artifact。首个插件为 ASR，端到端交付由 [penhin-code#25](https://github.com/penhin/penhin-code/issues/25) 跟踪。

| 插件 | 用途 | 状态 |
| --- | --- | --- |
| [asr](plugins/asr/README.md) | 将核心提供的音频 Attachment 转写为文本，供用户编辑后提交 | 规划中，未发行 |

## 职责

本仓库维护插件实现、依赖与模型资产锁、构建与发行材料、插件测试。`penhin-code` 维护录音、Attachment 生命周期、宿主协议、权限与项目策略，以及用户输入和提交。

ASR 的预期流程：用户按住说话 → 核心录音 → 松开生成音频 Attachment → 核心调用 ASR → 最终转写写入可编辑输入框 → 用户主动提交。插件不自行采集麦克风，也不自动发送模型请求。

## 仓库结构

```text
plugins.json                 插件目录元数据，不是安装器 manifest
plugins/asr/                 首个官方插件的规格与后续实现
docs/compatibility.md        已核对的核心接口与待补齐项
docs/releases.md             按插件发行的约定
scripts/check_repository.py  目录元数据及本地文档链接检查
.github/workflows/ci.yml     Windows / Linux 基础检查
```

添加插件与开发检查见 [CONTRIBUTING.md](CONTRIBUTING.md)。开始实现 ASR 前，请阅读 [ASR 交付规格](plugins/asr/README.md) 和 [核心兼容性说明](docs/compatibility.md)。

## 发行

每个插件使用 `<plugin-id>/v<version>` 标签，例如 `asr/v0.1.0`。这只是命名约定；当前尚未创建标签或发布制品。

用户最终安装的是单个插件的已验证 Artifact。当前核心不支持从本仓库 Git URL 直接选择 `plugins/asr`；本仓库根目录也不是一个可安装插件。制品根目录和安装步骤见 [发行约定](docs/releases.md)。

源码使用 [MIT License](LICENSE)。未来引入的模型和依赖须分别记录其许可与再分发条件。
