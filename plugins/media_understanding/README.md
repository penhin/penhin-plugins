# 媒体理解

面向 [Penhin](https://github.com/penhin/penhin) 的独立 Python 插件，需求来自 [penhin-code#41](https://github.com/penhin/penhin-code/issues/41)。版本 `0.1.0`，开发中、未发行。

图片、PDF 和视频经过本地解码与视觉理解，输出有顺序、带来源的 **不可信 Context Supplement**。核心服务不依赖 Penhin；薄适配层返回真正的 `penhin.Plugin`，通过 `penhin.plugins` entry point 显式加载。

## 能力与后端

- PNG、JPEG、静态 WebP：修正 EXIF 方向、缩放、透明背景铺白，交给视觉后端识别文字、描述画面和图表。
- PDF：按页提取原生文字并渲染画面，因此扫描件和有图表的页面也进入视觉理解；保留页码。不把“有文字”误判为“没有视觉内容”。
- MP4、WebM：本地 PyAV 解码，按间隔抽帧，保留时间戳；明确返回 `video_frames_sampled`、`video_audio_not_processed`。不是完整视频时序理解，音轨需宿主另行交给 ASR。
- 宿主已提取的视频帧：传图片 MIME 和 `timestamp_seconds` 即可。

解析器负责媒体格式，不负责语义理解。`VisionBackend.describe(Visual) -> str` 是可替换的异步接口，可接 OCR、直接加载的本地视觉模型或本机推理服务；采用 OCR 后端时，能力只限文字，不能宣称完成图表/场景理解。内置 `OllamaBackend` 是可选实现，需要宿主指定已安装的本地视觉模型，不指定默认模型、不下载模型、不启动服务。

## 安装

Python 3.12+（当前 PyAV 19 原生后端要求），在仓库根目录执行：

```sh
python -m pip install -e "./plugins/media_understanding[local,ollama,dev]"
python -m pip install ../penhin
```

`local` 固定 Pillow 13.0.0、pypdfium2 5.14.0、PyAV 19.0.2；`ollama` 固定 HTTPX 0.28.1。原生 wheel 支持决定实际可用平台。基础包没有强制解码器、HTTP 客户端或 Penhin 依赖，宿主可注入自己的 `Renderer` 与 `VisionBackend`。模型及许可证由宿主配置和管理；可用 `expected_digest` 核对本机 Ollama `/api/tags` 返回的模型摘要，不随包捆绑模型权重。

完整运行依赖的版本与 SHA-256 见 [requirements-local.lock](requirements-local.lock)，来源与许可见 [THIRD_PARTY.md](THIRD_PARTY.md)。发行安装可先 `python -m pip install --require-hashes -r plugins/media_understanding/requirements-local.lock`，再安装插件 wheel。

## 宿主提交前回退

```python
from penhin_media import MediaInput, MediaService
from penhin_media.ollama import OllamaBackend

# 从应用配置读取；必须是本机已经安装的视觉模型。
backend = OllamaBackend(model=config.media_model, expected_digest=config.media_model_digest)
service = MediaService(backend)

async def read_attachment(source_id: str) -> MediaInput:
    # 宿主负责当前项目/会话授权、过期检查和有界读取；不是插件提供的 broker。
    attachment = await host.attachments.read_authorized(source_id)
    return MediaInput(attachment.data, source_id, attachment.mime_type)

async def ensure_enabled() -> bool:
    # 已启用时直接返回；首次启用由宿主展示确认并持久化用户的明确选择。
    # 同时检查插件安装来源、授权和项目策略。拒绝/取消不能写入启用配置。
    return await host.ensure_media_plugin_enabled(project_id)

supplement = await service.prepare_fallback(
    attachment_ids,
    read_attachment=read_attachment,
    ensure_enabled=ensure_enabled,
    cancellation=cancellation_token,
)
# 仅在成功返回之后才构造并发送本次 Agent 请求。
# supplement.as_text_block() 追加到本次 user 消息的请求投影，不能写入 system。
# 宿主在会话退出时 await service.aclose()，或交给下面的 PluginRuntime 管理。
```

上例中的 `host`、`config` 是应用依赖的示意，不是 Penhin 公开 API。`ensure_enabled` 必须返回严格的 `True` 才放行。它在读取附件/启动解码前执行，处于同一次超时和取消范围。UI 若需要等待较长时间，可先由宿主完成确认，再让此回调只核对已保存的启用状态。

`understand()` 是已经授权后的显式调用接口，不自行确认项目。不要用模型工具错误来代替提交前 gate：Agent 可以在工具失败后继续回答；`prepare_fallback()` 抛错时宿主必须停止整次含媒体提交。

## Penhin 接入

```python
from penhin import PluginRuntime, load_plugin

async with PluginRuntime() as runtime:
    plugin = load_plugin("media_understanding", service=service)
    await runtime.activate(plugin)
    # 仅注册生命周期，默认不向模型暴露工具。
    # service.prepare_fallback(...) 成功后才运行 Agent。
```

若需要模型显式调用附件工具，向工厂传 `read_attachment(source_id, ToolContext)`，工具名为 `media_understanding__understand`；它只接受附件 ID 列表，不接受磁盘路径或 URL。宿主根据 `ToolContext.session_id/invocation_id` 做授权。`close_service=False` 表示共享服务由宿主关闭；否则停用/关闭插件会取消并等待服务当前请求的清理。模型服务等共享后端仍由宿主拥有。

当前 Penhin 的 `Agent.run` 接受文本 query。宿主应为**本次提交**建立 `Agent(transform_context=...)`，在请求投影中把 `supplement.as_text_block()` 追加到对应 user 消息，而不把媒体/提取内容持久化到普通 session 记录。投影不能修改 `context.system`，不能跨提交复用旧补充，也不应在每轮模型请求时重新解码。真实内核的此路径、entry point 和插件生命周期均由 [接入测试](tests/test_penhin.py) 验证。

本插件不实现 Provider 能力注册、原始媒体直传、Provider 拒绝后的重试、安装验签、附件加密存储和项目配置 UI。这些属于宿主；相关产品策略见 [#38](https://github.com/penhin/penhin-code/issues/38)、[#34](https://github.com/penhin/penhin-code/issues/34)。新 Penhin 没有旧版 `AttachmentBroker`，本插件的 `AttachmentReader` 只是注入边界，不能当作旧协议兼容实现。

## 结果、上限与清理

`ContextSupplement.as_dict()` 包含插件/版本、后端名、`trust: untrusted`、有序 `parts` 和覆盖范围警告。每段包含 `source_id`、MIME、从 0 起的附件及段落索引，PDF 页码从 1 起，视频时间戳为秒；原生 PDF 文字与视觉描述分别保留。来源标签不构成防提示词注入的隔离机制。

默认每批 8 个附件、单附件 32 MiB、整批 64 MiB；单图 2000 万像素、发送视觉模型前最长边 1536；PDF 最多 16 页；视频最多 120 秒、每 5 秒采样、24 帧、解码帧数最多 7200；整批最多 32 段。单段描述及 PDF 原生文字各不超过 16384 字符，整批正文不超过 65536 字符；完整请求默认超时 180 秒。用 `MediaLimits` 配置，宿主还须在读取前执行自身上限。

超限、无效格式、空结果、任一附件失败均抛出稳定的 `MediaError.code`；不返回部分成功、不静默截断。需要密码的 PDF、动画 WebP、多个视频轨、其他未列 MIME 当前拒绝。视频是显式采样，不保证覆盖短暂事件。每个服务同一时间只处理一批，忙时 `media_busy`；关闭后 `media_closed`。服务绑定单个事件循环。

解码进程只使用内存和管道，不写临时媒体文件；stdout 只传有界结果，第三方解码日志被丢弃。取消、超时和关闭会终止并回收解码进程，等待后端合作取消完成。子进程是资源生命周期边界，不是 OS 沙箱。Ollama 仅接受回环 HTTP 地址，关闭环境代理及重定向；外部守护进程和共享模型的驻留时间由宿主管理，断开客户端不保证立即停止其 GPU 工作。别名是否指向远端仍依赖可信的本机服务配置。

## 验证

```sh
python -m pytest plugins/media_understanding/tests -m "not model"
python -m ruff check plugins/media_understanding
python -m build plugins/media_understanding
python scripts/check_repository.py
```

基础测试使用真实 PNG/PDF/MP4 解码和真实 Penhin `PluginRuntime`/`Agent`；视觉语义后端用可控替身，HTTP 合同用 HTTPX transport。实际模型测试单独启用（PowerShell）：

```powershell
$env:PENHIN_MEDIA_MODEL = '你的已安装视觉模型名'
# 可选：PENHIN_MEDIA_MODEL_DIGEST / PENHIN_MEDIA_OLLAMA_URL
python -m pytest plugins/media_understanding/tests -m model
```

模型 smoke 检查合成红色图片是否识别为红色，不能代替真实文档/图表质量评测。尚未验证真实模型推理及宿主首用确认 UI、安装验签、直传/回退重试的产品闭环，因此不能据此关闭 #41。

实现参考：[Ollama 视觉 API](https://docs.ollama.com/capabilities/vision)、[Chat API](https://docs.ollama.com/api/chat)、[模型列表](https://docs.ollama.com/api/tags)、[PDFium Python API](https://pypdfium2.readthedocs.io/en/stable/python_api.html)、[Pillow Image API](https://pillow.readthedocs.io/en/stable/reference/Image.html)、[PyAV 容器 API](https://pyav.org/docs/stable/api/container.html)。
