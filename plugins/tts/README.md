# TTS 中文语音合成

本地 MeloTTS（sherpa-onnx / CPU）服务：完整文本生成 WAV、增量文本分段合成、顺序播放与打断，以及 ASR → 回答 → TTS 的组合接口。版本 `0.1.0`，开发中，未发布。

## 快速开始

在仓库根目录、Python 3.11+ 虚拟环境中执行：

```sh
python -m pip install -e "./plugins/tts[melo,playback,dev]"
python -m penhin_tts.download --output .cache/tts/melo
python -m penhin_tts --model-dir .cache/tts/melo --text "你好，欢迎使用中文语音。" --output hello.wav
python -m penhin_tts --model-dir .cache/tts/melo --text "这句话可以被打断。" --play
```

`--play` 使用默认扬声器，Ctrl+C 打断。`--stream --play` 或 `--stream --output answer.wav` 从标准输入接收增量文本。输出文件须不存在。没有音频设备时可只安装 `[melo]` 并输出 WAV。

固定模型 `vits-melo-tts-zh_en`，单一音色、44.1 kHz，支持中文和词表内英文，速度 `0.5–2.0`。选择它是为了复用 ASR 的运行时，在 CPU 上离线部署；未提供音色克隆、情绪控制或 GPU 配置。首段包含模型校验和加载开销，未承诺实时率。

模型压缩包约 159 MiB，ONNX 文件约 163 MiB；下载器只解包固定清单并校验 SHA-256，运行时再次校验。下载是显式操作，推理不联网。已有压缩包可使用 `--archive path/to/model.tar.bz2`，目标目录必须不存在。权重、录音和生成音频不进入源码包。

## 完整文本与增量文本

```python
from pathlib import Path
from penhin_tts import MeloBackend, TtsService

async def make_wav(answer: str) -> bytes:
    async with TtsService(MeloBackend(Path(".cache/tts/melo"))) as tts:
        audio = await tts.synthesize(answer)
        return audio.to_wav()

async def stream_answer(answer_deltas, send_audio):
    async with TtsService(MeloBackend(Path(".cache/tts/melo"))) as tts:
        async with tts.stream(answer_deltas, response_id="answer-42") as segments:
            async for segment in segments:
                await send_audio(segment)
```

`answer_deltas` 为 `AsyncIterable[str]`，每项是**新增文本**，不能传累计全文快照。完整字符串也可传给 `stream()` 分段播放。输出 `SpeechSegment` 包含 `response_id`、从 0 开始的 `index`、本段 `text`、`pcm`、`sample_rate` 和 `duration_seconds`。PCM 为单声道 PCM16 little-endian，无 WAV 头；`to_wav()` 可包装为独立 WAV。正常迭代结束表示合成完成，不能据此推断远端播放已经结束。

这是真实的增量文本输入与分段音频输出：不等全文结束即可输出第一段。MeloTTS 本身每段离线推理，不是单个字/音频帧级的神经网络双流式模型。切段优先中文句末标点和换行；较长子句可在逗号切开；最长 120 字，已有未成句文本默认等待 600 ms 后发出，尾段在 EOF 发出。无标点长文本也有长度上限。断段可能影响语调和数字/英文的连贯性，宿主应提供可朗读的正文，过滤代码块、工具消息和不想播出的内容。

默认上限为每回答 8000 字、累计 600 秒音频、每段 60 秒；使用 `TtsLimits` 可缩小。空白/纯标点忽略，空回答返回零帧 WAV。每服务仅一个回答，忙时 `TtsBusy`；绑定一个事件循环，关闭后不能复用。结果队列容量为 1，加上一个正在合成/等待入队的段和当前播放段；消费慢会暂停文本读取。宿主自身的文本与音频队列也必须有上限。

`timeout=60` 分别限制每次等待文本增量和每段合成（首段含加载）；不把整场对话或慢速播放限制成 60 秒。`async with tts.stream(...)` 是必需的，提前 break、异常、任务取消都会回收子进程。文本源由宿主拥有并关闭；服务会取消当前等待的 `anext`。

## 播放与打断

```python
from penhin_tts import SoundDeviceSink, SpeechPlayer

async def speak_answer(tts, deltas):
    async with SpeechPlayer(tts, SoundDeviceSink()) as player:
        await player.speak(deltas, response_id="answer-42")
        # 并行的宿主按钮/VAD 事件可 await player.interrupt()
```

`SpeechPlayer.speak()` 等播放结束；`interrupt()` 停止当前设备音频，清空待播段，取消合成，并等本轮清理完毕，之后可再 `speak()`。`task.cancel()`、`cancellation=token`（布尔属性 `cancelled`）和 `aclose()` 也支持打断。每个回答使用独立工作进程，原生推理可以终止；不是只停止收集结果。进程在一轮的各段间复用，跨回答重新加载。

网页/移动宿主可实现 `AudioSink`：`async write(segment)` 必须等该段播放完毕，`async stop()` 必须停止可听音频并清空设备/网络队列；两个方法都要支持取消。远端应在 stop 时确认清空，并用 `response_id` 拒绝旧轮迟到的数据。`SoundDeviceSink` 是本机实现，512 帧回调，stop 调用 PortAudio abort。实际停声延迟仍由声卡和驱动决定。

`tts.interrupt()` 只取消合成；需要停声时调用 `player.interrupt()`。直接使用服务流时，打断后仍需退出原 `async with` 再启动新流。Player 拥有它的 service 与 sink，不可跨 Player 共享。销毁 Player 会关闭 service。

## 与 ASR 组合

```python
import asyncio
from pathlib import Path
from penhin_asr import AsrService, AudioInput, ZipformerBackend
from penhin_tts import MeloBackend, SoundDeviceSink, SpeechPlayer, TtsService, VoiceInteraction

async def voice_turn(recorded_wav, respond):
    # respond(final_text) 返回 async generator：只 yield 新增的可朗读回答正文。
    async with AsrService(ZipformerBackend(Path(".cache/asr/zipformer"))) as asr:
        player = SpeechPlayer(TtsService(MeloBackend(Path(".cache/tts/melo"))), SoundDeviceSink())
        voice = VoiceInteraction(asr, player, respond)
        try:
            await voice.run_turn(AudioInput(recorded_wav, "recording-1"), response_id="turn-1")
        finally:
            await voice.aclose()
```

`run_turn()` 仅在宿主确认用户提交后调用，将 ASR 最终文本传给回答生成器并播出增量回答；没有识别到文字则不调用回答生成器。它借用 ASR，拥有 Player；关闭时 ASR 仍由宿主管理。`await voice.interrupt()` 可从录音开始/VAD 检测到说话/停止按钮触发，同时取消当前 ASR、回答流和播放，随后允许新轮。回答生成器必须在 `finally` 中取消其单独启动的 Agent/网络请求；控制器会调用其 `aclose()`。

真实 Penhin 的 `text_delta` 事件桥接见 [penhin_voice.py](examples/penhin_voice.py)：将 `make_responder(model, session, runtime)` 传入 `VoiceInteraction`。该示例使用容量为 1 的队列，将 Agent 回调变为异步增量源，并在打断时取消 Agent。宿主提供已配置的 `ModelClient`，示例不读取凭据，也不硬编码云端模型。

流式 ASR 的中间结果是可修订的全文快照，不能直接当作回答 deltas 送给 TTS。宿主可先通过 `asr.stream()` 取得最终转写，再以最终文本调用自己的回答流并交给 Player。麦克风采集、VAD、回声消除、附件权限、录音提交和 UI 由宿主提供；本包不是已集成的全双工语音客户端。扬声器模式需宿主做回声处理，或使用耳机/按住说话。

## Penhin Plugin

```python
from penhin import PluginRuntime, load_plugin

async def activate(tts, player):
    async with PluginRuntime() as runtime:
        await runtime.activate(load_plugin("tts", service=tts, player=player))
        # 宿主直接调用 player.speak(回答正文或增量流)。停用时关闭 Player 和 service。
```

默认只注册生命周期，普通回答自动朗读由宿主接入。可选 `publish_audio(audio, context)` 异步回调会启用 `tts__synthesize(text, speed)` Agent 工具；回调按真实 `ToolContext` 的 session 权限保存 `audio.to_wav()`，返回附件 ID。工具返回附件 ID、格式和时长，不把音频 Base64 塞进模型上下文，不接受路径，也不触发扬声器。保存回调须可取消并清理部分写入。若传入 `player`，它必须使用同一个 service；`close_service=False` 仅适用于宿主明确管理共享生命周期的情况。

工厂按需导入 Penhin，服务不依赖内核。当前内核基线见[兼容性记录](../../docs/compatibility.md)；需从匹配源码安装。Agent 运行中先取消并等待结束，再停用插件。

## 验证与来源

```sh
python -m pytest plugins/tts/tests -q
python -m ruff check plugins/tts/src plugins/tts/tests plugins/tts/examples
python -m ruff format --check plugins/tts/src plugins/tts/tests plugins/tts/examples
python -m build plugins/tts
python scripts/check_repository.py
```

设置 `PENHIN_TTS_MODEL_DIR` 后执行真实模型测试；未配置时明确跳过。基础 CI 不下载模型，Penhin 未安装时集成测试明确跳过。声卡播放与麦克风回声场景需要在宿主设备验收，替身测试不代表人耳听感验证。

- [MeloTTS 官方项目](https://github.com/myshell-ai/MeloTTS)：MIT；[ONNX 模型官方说明](https://k2-fsa.github.io/sherpa/onnx/tts/pretrained_models/vits.html#vits-melo-tts-zh-en-chinese-english-1-speaker)。发布压缩包含 MyShell.ai MIT LICENSE，下载时保留。
- [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)：Apache-2.0；NumPy：BSD-3-Clause；[sounddevice](https://python-sounddevice.readthedocs.io/)：MIT，PortAudio 使用其自身许可。
- [模型锁](src/penhin_tts/model-lock.json)记录模型、词表、中文规则、分词词典与说明文件摘要；[运行时锁](requirements-melo.lock)固定合成后端和可选播放依赖及包摘要。
