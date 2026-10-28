# Penhin Plugins

为 [Penhin](https://github.com/penhin/penhin) 提供可复用能力服务与 Python Plugin 适配。宿主可直接调用服务，也可让 Agent 通过显式启用的插件工具调用。

| 插件 | 能力 | 状态 |
| --- | --- | --- |
| [ASR](plugins/asr/README.md) | Zipformer 中文离线转写，支持流式与整段 | 开发中，已有服务、CLI 与 Penhin 适配，未发布 |
| [媒体理解](plugins/media_understanding/README.md) | 图片、PDF 页面、视频采样帧的本地理解；输出带来源的不可信上下文补充 | 开发中，已有服务、解码器、可替换视觉后端与 Penhin 适配，未发布 |

## 开始使用 ASR

在已激活的 Python 3.11+ 虚拟环境中：

```sh
python -m pip install -e "./plugins/asr[zipformer]"
python -m penhin_asr.download --output .cache/asr/zipformer
python -m penhin_asr .cache/asr/zipformer/test_wavs/0.wav --model-dir .cache/asr/zipformer
```

模型只在显式下载步骤联网，识别在本地完成。整段输入为单声道 PCM16 WAV；流式输入为异步 PCM 字节块，返回中间全文快照和最终文本。详细限制、宿主 API、取消与 Penhin 接入见 [ASR 使用说明](plugins/asr/README.md)。

宿主可选择“转为文字后编辑提交”或“直接交付语音”：后者通过 `prepare_voice()` 在内部转写，向模型发送文字，界面仍展示语音消息。插件提供识别与提交准备，录音、附件权限、交付方式设置和实际模型调用由宿主负责。后续 TTS 可接在模型回复之后；完整语音交互仍需宿主接入。

## 开发与交付

媒体理解的安装、宿主提交前回退和可选 Ollama 后端见 [媒体理解说明](plugins/media_understanding/README.md)。插件面向当前 Penhin；产品验收来源仍是 `penhin-code` 的 [#41](https://github.com/penhin/penhin-code/issues/41) 和 [#42](https://github.com/penhin/penhin-code/issues/42)。

每个插件在 `plugins/<id>/` 内独立维护 Python 包、测试、模型锁与版本。根 `plugins.json` 是仓库目录元数据，不是安装 manifest。

- [参与开发](CONTRIBUTING.md)
- [兼容性与验证记录](docs/compatibility.md)
- [发行约定](docs/releases.md)
- [2026-10-28 插件需求调研](docs/research/penhin-plugin-issues-2026-10-28.md)

源码使用 [MIT License](LICENSE)。依赖与模型使用各自许可，模型权重不包含在仓库或插件 wheel 中。
