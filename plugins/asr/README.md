# ASR 语音转写

本地离线、中文优先的转写服务，使用 sherpa-onnx + 流式 Zipformer int8，同一模型支持整段和流式输入，并提供 Penhin Plugin 适配。当前版本 `0.1.0` 处于开发中，尚未发布。

宿主可提供两种交付方式：把转写文字显示到编辑框，供用户修改后提交；或者让用户直接提交语音，在后台转写后把文字发送给模型，界面保留语音消息。插件提供两种方式共用的识别与提交准备能力，不采集麦克风、不自行调用模型、不把转写放进系统提示词。产品需求来自 [penhin-code#42](https://github.com/penhin/penhin-code/issues/42)。

## 安装与真实音频体验

以下命令在仓库根目录、已激活的 Python 3.12 虚拟环境中执行。服务包本身支持 Python 3.11+；原生后端可用平台受 sherpa-onnx / NumPy wheel 限制，已验证平台见[兼容性记录](../../docs/compatibility.md)。

```sh
python -m pip install -e "./plugins/asr[zipformer,dev]"
python -m penhin_asr.download --output .cache/asr/zipformer
python -m penhin_asr .cache/asr/zipformer/test_wavs/0.wav --model-dir .cache/asr/zipformer
# 逐行输出中间文本，最后一行 is_final 为 true；文件按计算速度处理。
python -m penhin_asr .cache/asr/zipformer/test_wavs/0.wav --model-dir .cache/asr/zipformer --stream
```

固定模型为 `sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30`。压缩包约 133 MB，三个 ONNX 文件合计约 167 MB（文件大小，不是内存占用）。下载和解包均校验固定 SHA-256；进程启动时再次校验模型和词表，运行期不联网。可用 `--archive /path/to/model.tar.bz2` 从已下载压缩包离线准备，目标目录必须不存在。锁文件随包交付：[model-lock.json](src/penhin_asr/model-lock.json)。

## 宿主直接调用

```python
from pathlib import Path
from penhin_asr import AsrService, AudioInput, ZipformerBackend

async def transcribe_recording(wav_bytes: bytes) -> str:
    async with AsrService(ZipformerBackend(Path(".cache/asr/zipformer"))) as service:
        result = await service.transcribe(AudioInput(wav_bytes, source_id="recording-1"))
        return result.text  # 宿主回填输入框，等待用户编辑和提交
```

`AsrService` 绑定一个事件循环，可在同一循环中反复使用；关闭后不能重开。每个服务同一时间只处理一个请求，忙时抛出 `AsrBusy`。不同会话需要独立服务，宿主再限制总并发。

输入为单声道、16-bit little-endian PCM WAV，采样率 8–48 kHz；默认最大 10 MiB、120 秒，可用 `AudioLimits` 缩小。宿主负责将 MP3、浏览器 WebM 等录音转换为该合同；首版不隐式安装或调用 FFmpeg。空音频拒绝，无可识别语音时可以返回空字符串。

整段结果包含 `text`、`source_id`、`duration_seconds`、`is_final: true`。整段输入在内部逐块送入同一个流式解码器，按计算速度处理，结束时补齐右侧上下文并输出最终文本。结果仍为不可信用户数据。

## 流式调用

```python
from pathlib import Path
from penhin_asr import AsrService, ZipformerBackend

async def transcribe_live(pcm_chunks, show_text):
    # pcm_chunks 是宿主管理的异步音频源，产生 PCM16 字节块；不包含 WAV 头。
    async with AsrService(ZipformerBackend(Path(".cache/asr/zipformer"))) as service:
        async with service.stream(
            pcm_chunks, sample_rate=16000, source_id="recording-1"
        ) as updates:
            async for result in updates:
                show_text(result.text, is_final=result.is_final)
```

- 采样率在整个会话中固定，支持 8–48 kHz；单声道、16-bit little-endian PCM。每块必须非空、偶数字节，最多 1 秒；建议宿主送入 20–320 ms 的块。累计仍受 10 MiB / 120 秒限制。
- 每条结果是**当前全文快照**，宿主应替换上一条文本，不能拼接；中间结果可以为空或与上一条相同，也允许修订。`duration_seconds` 只计算真实输入，不含结尾补零。
- 输入源结束表示停止录音，触发解码收尾；只在最后返回一条 `is_final=True`。会话不按静音自动切句，宿主需要结束当前音频源才能确定最终结果。
- 必须使用 `async with`。提前 `break`、异常或取消会回收工作进程，且不会伪造最终结果。服务关闭时也能中止等待输入或等待消费的流。音频源及其采集设备由宿主负责关闭。
- 内部只缓存一条待消费结果，消费慢时暂停读取后续输入。宿主的麦克风队列也应有容量上限；不能在这里无限积压录音。

流式用于宿主直接更新输入框；Agent 的 `asr__transcribe` 工具仍接收已有附件并返回最终结果。

## 可选文字展示与直接语音交付

“是否显示转写文字”和“如何输入音频”是独立选择：整段或流式识别都可以用于以下两种交付方式。

| 交付方式 | 用户看到的内容 | 宿主行为 |
| --- | --- | --- |
| 转为文字 | 可编辑的转写文字 | 调用 `transcribe()` / `stream()`，用户编辑并提交后发送文字 |
| 直接语音 | 语音消息、时长与播放入口 | 用户提交语音后调用 `prepare_voice()`，后台把最终文字发送给模型，无需额外文字确认 |

```python
from penhin import Agent, PluginRuntime, load_plugin

async def deliver_voice(service, model, session, source_id, read_authorized_audio, cancellation):
    async with PluginRuntime() as runtime:
        await runtime.activate(load_plugin("asr", service=service))
        # 此方式只注册生命周期，不向模型暴露 ASR 工具。
        # read_authorized_audio(source_id) 是宿主绑定当前会话的异步授权读取函数，
        # 返回 AudioInput；读入限制不超过 service.limits.max_bytes。
        prepared = await service.prepare_voice(
            source_id, read_audio=read_authorized_audio, cancellation=cancellation,
        )
        # 展示数据只有 type=audio、source_id、duration_seconds，不含转写文字。
        display_message = prepared.as_display_message()
        agent = Agent(model, plugins=runtime)
        # 标准 Penhin 文本入口；转写作为 user 内容进入内部执行历史。
        result = await agent.run(session, prepared.model_text, cancellation=cancellation)
        return display_message, result
```

上例在退出时关闭服务；需要跨提交复用时，由宿主把 `PluginRuntime` 的生命周期放在外层。示例不替宿主绘制界面或存储音频。`VoiceSubmission` 是提交准备结果，不是 Penhin 内建消息类型；`as_display_message()` 供宿主自己的消息视图使用，不能把这个 audio 字典直接传给当前 `Agent.run()`。原始录音、播放地址、业务消息 ID、Session/轮次关联以及“发送中 / 失败 / 完成”的状态由宿主管理。语音界面应使用自己的展示数据，不直接渲染内部 Session 的转写文字。

`prepare_voice()` 将附件读取和识别纳入同一个超时、取消和并发占用范围，并验证返回的附件 ID。读入失败、ID 不匹配、格式错误、超限、取消或无可识别语音（`no_speech`）均抛错，宿主必须停止这次提交，不能用空文字继续调用模型。它只准备一次，不在 `transform_context` 中每轮重跑识别。ASR 成功也不代表 Agent 已完成；模型执行中断后应根据 Session 状态续跑已有轮次，不能再次追加相同语音文字。

如果录音期间已经进行了流式识别，可直接复用最终结果：

```python
from penhin_asr import VoiceSubmission

# final_transcript 必须来自同一条已授权录音，且 is_final=True。
prepared = VoiceSubmission.from_transcript(final_transcript)
# prepared.model_text 供模型；prepared.as_display_message() 供语音界面。
```

该转换拒绝中间结果和空文本，不重复识别。宿主只在用户已选择直接交付语音并执行发送动作后提交；流式中间结果不能触发 Agent。转写属于用户输入，不能提升为系统指令。

后续 TTS 接在模型回复之后：`用户语音 → ASR → 模型文字请求 / 回复 → TTS → 回复语音`。输入是否显示文字、回复是否播放语音由宿主分别选择；ASR 不强制绑定 TTS，本轮未实现 TTS、语音 UI 或宿主设置持久化，也未修改 Penhin 内核。

## 接入 Penhin

先安装匹配的 Penhin 内核（当前为私有源码仓库，不能假定 PyPI 可用）：

```sh
python -m pip install ../penhin
```

```python
from pathlib import Path
from penhin import Agent, PluginRuntime, load_plugin
from penhin_asr import AsrService, AudioInput, ZipformerBackend

async def use_asr(model, attachments, session):
    service = AsrService(ZipformerBackend(Path(".cache/asr/zipformer")))

    async def read_audio(attachment_id, context):
        # 宿主检查 session/project 权限、过期和撤销，并限制读取量。
        data = await attachments.read_authorized_wav(
            attachment_id, session_id=context.session_id,
            invocation_id=context.invocation_id, max_bytes=service.limits.max_bytes,
        )
        return AudioInput(data, attachment_id)

    plugin = load_plugin("asr", service=service, read_audio=read_audio)
    async with PluginRuntime() as runtime:
        await runtime.activate(plugin)
        agent = Agent(model, plugins=runtime)
        # 模型可调用 asr__transcribe(attachment_id)，参数不接受宿主路径。
        return await agent.run(session, "转写指定的音频附件")
```

`attachments.read_authorized_wav` 是示意的宿主接口，不是 Penhin 内置 API。工厂不传 `read_audio` 时只注册生命周期，适合直接语音交付；显式传入才注册 `asr__transcribe`。工具的 `read_audio` 必须为异步函数，收到真实 `ToolContext`，并返回 ID 一致的 `AudioInput`。插件不会替宿主判断附件权限。也可通过 `load_plugin("penhin_asr:create_plugin", ...)` 加载。

工厂默认 `close_service=True`，停用时关闭传入服务并取消直接发起的在途识别。仅在宿主明确管理共享服务生命周期时设置 `False`。Penhin 运行中禁止插件热停用：先取消并等 Agent 返回，再 deactivate；重新启用使用新服务。直接转写并不需要调用 Agent 或读取模型凭据。

## 取消、超时与错误

`task.cancel()`、`await service.aclose()`，以及 `transcribe(..., cancellation=token)` / `stream(..., cancellation=token)` 均可取消；token 只需提供布尔属性 `cancelled`，可使用 Penhin 的 `CancellationToken`。超时默认 60 秒：整段调用限制总执行时间；流式调用限制每次等待下一条解码结果的时间（包含等待输入，首条包含模型校验和加载），不把正常录音的总时长限制为 60 秒。

每次整段调用或流式会话创建一个专用 CPU 工作进程，在该会话内保留模型和解码状态，完成或取消后退出并回收。不会为每个音频块重新加载模型。关闭期间保留占用，拒绝迟到结果；取消传播为 `asyncio.CancelledError`，超时为 `AsrTimeout`。这用于中断原生推理，不是 OS 沙箱。不同录音会话仍需重新加载，尚未做跨会话预热。

其他错误携带稳定 `AsrError.code`，例如 `invalid_wav`、`audio_too_large`、`model_missing`、`model_mismatch`、`backend_not_installed`。默认错误与日志不包含音频、转写内容或宿主路径；CLI 的 JSON 转写输出是用户显式请求的结果。

## 开发验证

```sh
python -m pytest plugins/asr/tests -q
python -m ruff check plugins/asr/src plugins/asr/tests
python -m ruff format --check plugins/asr/src plugins/asr/tests
python -m build plugins/asr
python scripts/check_repository.py
```

真实模型测试需设置 `PENHIN_ASR_MODEL_DIR` 为模型目录，再运行 `python -m pytest plugins/asr/tests/test_model.py -q`。未设置会明确跳过；未安装 Penhin 时集成模块也明确跳过。完整验收必须安装内核并执行它们，不能用基础 CI 的绿色替代。[发行约定](../../docs/releases.md)说明包、依赖与宿主验收的边界。

后续宿主工作包括真实按住录音、松开/取消手势、Attachment 生命周期、输入框编辑提交以及安装授权。当前服务与适配完成不意味着整个 #42 已完成。

## 模型与依赖来源

- [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)：Apache-2.0；使用其[流式模型处理文件示例](https://github.com/k2-fsa/sherpa-onnx/blob/master/python-api-examples/online-decode-files.py)中的 OnlineRecognizer 接口。
- [Zipformer 中文模型](https://k2-fsa.github.io/sherpa/onnx/pretrained_models/online-transducer/zipformer-transducer-models.html)：官方发布包没有单独 LICENSE；其 README 指向的[上游模型卡](https://huggingface.co/yuekai/icefall-asr-multi-zh-hans-zipformer-large)本次返回 401，权重许可尚未核实，不能从运行时代码的 Apache-2.0 或本仓库 MIT 推断。下载器保留上游 README 与来源记录；权重和样本不提交、不打入 wheel，分发权重前需另行确认许可。
- NumPy：BSD-3-Clause。安装所需依赖版本在 [pyproject.toml](pyproject.toml) 固定；完整后端依赖及摘要见 [requirements-zipformer.lock](requirements-zipformer.lock)。
