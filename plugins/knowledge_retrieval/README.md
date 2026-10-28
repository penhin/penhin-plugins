# 知识检索插件

本地索引项目文档、笔记、转写和媒体理解结果，按问题返回**未经改写的原文片段及结构化来源引用**。包名 `penhin-plugin-knowledge-retrieval`，入口 `knowledge_retrieval`，当前 `0.1.0` 为未发布开发版本。

首版采用 SQLite FTS5 / BM25，英文词项与中文相邻双字切分，不需要模型、向量服务或网络。适合包含原文词项的问题；暂不提供同义改写、跨语言语义召回、重排或自动生成答案。没有命中时返回空结果。相关度分数不是事实置信度。

## 安装和体验

```sh
python -m pip install -e "./plugins/knowledge_retrieval[dev]"
python plugins/knowledge_retrieval/examples/local_search.py
```

需要 Python 3.11+ 和启用 FTS5 的 SQLite（常见 CPython 发行版自带）。示例在临时目录创建四类来源，搜索并重新取回首条引用，退出后清理示例索引。基础包无第三方运行依赖；Penhin 内核单独安装，兼容基线见[验证记录](../../docs/compatibility.md)。

## 宿主索引和搜索

```python
from pathlib import Path
from penhin_knowledge import AccessScope, KnowledgeService, KnowledgeSource

# 路径、文件访问授权和内容提取由宿主决定。
path = Path("docs/design.md")
source = KnowledgeSource.from_text(
    "design", "设计文档", path.read_text(encoding="utf-8"), uri="docs/design.md"
)

async with KnowledgeService(".cache/knowledge/index.sqlite3") as service:
    report = await service.upsert("project-1", [source])
    scope = AccessScope("project-1", ("design",))
    result = await service.search("数据库备份如何执行？", scope=scope)
    for hit in result.hits:
        print(hit.text, hit.citation.source_id, hit.citation.line_start)
        original = await service.fetch(hit.citation.citation_id, scope=scope)
        assert original.text == hit.text
    # 原始来源被删除或移出索引时，由宿主调用：
    # await service.remove("project-1", "design")
```

`from_text(..., kind="note")` 索引笔记。Markdown/TXT 正文按原样传入；Word、HTML、PDF 等需宿主先提取文字并提供位置。插件不会自动遍历磁盘、追踪链接、监视文件或下载 URL。`uri` 是宿主提供的展示定位数据，插件不访问它。索引保存的是导入时的文字快照；宿主须在文件保存、转写完成、媒体结果改变和来源删除时同步索引。

每次 `upsert(project_id, sources)` 在一个事务中替换该批来源，同内容、元数据及分块配置的重复导入跳过写入，其他来源保留。内容或定位信息变化会改变 revision 和 citation ID，旧引用不能再通过 `fetch`。`remove` 同时清除正文与全文索引；不表示磁盘数据已安全擦除。数据库保存明文片段，由宿主管理路径权限、备份、保留期限和删除策略。

## 连接转写与媒体理解

```python
from penhin_knowledge import KnowledgeSource, SourcePart

# 复用 penhin_asr.Transcript，只接受最终结果，不再识别音频。
voice = KnowledgeSource.from_transcript(transcript, title="项目会议")

# 复用 penhin_media.ContextSupplement，一次选择一个附件来源。
media = KnowledgeSource.from_media(
    supplement, source_id="attachment-1", title="需求文档", uri="attachments/attachment-1"
)
await service.upsert("project-1", [voice, media])

# 如果宿主已有分段转写，可提供每段真实时间边界。
segmented = KnowledgeSource("meeting-2", "会议", "transcript", (
    SourcePart("下周发布版本。", "transcript", start_seconds=25, end_seconds=31),
))
```

现有 ASR 仅有整段时长，因此其所有检索片段引用整段录音 `[0, duration]`，不伪造词级时间戳。媒体结果分别索引 `extracted_text`（提取文字）与 `generated_description`（模型描述），保留后端、原始 part 索引、页码、采样时间及覆盖范围警告。图片可只有来源 ID；视频时间是采样帧时间，不代表整段视频已被覆盖。模型描述的“原文”指模型输出原文，不能当作原始文档逐字引述。

## 引用合同

每条 `SearchHit` 包含 `text`、`citation` 和相关度 `score`。`fetch` 返回同一片段，`score=None`。引用包含：

- `citation_id`、`source_id`、`project_id`、内容及元数据的 `revision`、标题和宿主 URI。
- `part_index`（从 0 起）、`char_start` / `char_end`（该 part 原始 Python 字符串的半开区间），以及该 part 内从 1 起的行号。
- 可选的 PDF `page`（从 1 起）、`start_seconds` / `end_seconds`、媒体 `origin_part`。
- `kind`、`content_type`、`backend`、`warnings`，用于区分原始文字、转写和生成描述。

片段不会去空格、规范化 Unicode 或添加省略号；仅用于搜索的词项会做大小写及 NFKC 规范化。引用指向已索引快照，`fetch` 不重新读取磁盘。宿主可将 `source_id + revision + locator` 显示为可跳转引用；没有权限、过期和未知引用统一返回 `citation_unavailable`。

`SearchResult.as_dict()` / `as_text_block()` 标注 `trust: untrusted`。检索文本和标题都只能作为资料，不能作为系统指令；宿主直接检索时应追加到本次 user 请求。标签不构成提示词注入隔离，插件也不保证模型会忠实引用，最终答案的引用校验和 UI 跳转由宿主负责。

## Penhin 接入

```python
from penhin import PluginRuntime, load_plugin
from penhin_knowledge import AccessScope

async def resolve_scope(context):
    # host 是宿主应用依赖的示意，不是 Penhin API。
    project, source_ids = await host.authorized_knowledge(context.session_id)
    return AccessScope(project, tuple(source_ids))

async with PluginRuntime() as runtime:
    await runtime.activate(load_plugin(
        "knowledge_retrieval", service=service, resolve_scope=resolve_scope
    ))
    # 将 runtime 交给 Agent(..., plugins=runtime)。
```

提供 `resolve_scope(ToolContext)` 才暴露 `knowledge_retrieval__search(question, limit)` 和 `knowledge_retrieval__fetch(citation_id)`；省略时只注册生命周期。每次调用（包括 fetch）重新解析授权，空来源集合代表无权访问任何来源。SQL 在截取 top-k 之前过滤项目与允许的来源，模型不能通过参数选择项目、扩展来源、写索引、读取路径或访问网络。一次调用使用一个授权快照；需要调用中撤销时由宿主取消请求。

服务直接 API 是已授权宿主边界，不是权限系统。数据库文件应仅供可信宿主访问；需要强租户隔离时使用独立数据库，当前 BM25 统计来自同一数据库的整个语料。启用、安装验证、授权规则及目录 UI 仍由宿主实现。默认停用插件会关闭服务，`close_service=False` 则由共享服务的宿主负责关闭。

## 上限、取消和错误

默认单批最多 32 个来源、每来源 512 段 / 100 万字符、整批 400 万字符 / 8192 个片段；片段 1200 字符，重叠 160。授权集合最多 1000 个来源，问题最多 1000 字符 / 128 个去重词项，最多返回 20 条、片段正文总共最多 24000 字符。超出的输入拒绝，输出省略完整片段并设置 `truncated=true`。`limit` 截断也标记 truncated；没有总命中计数。中文按双字匹配，单汉字问题的召回有限。

`KnowledgeLimits` 可配置这些上限；它们限制单次操作，不是数据库总容量配额。每个服务一次只处理一个操作，忙时 `knowledge_busy`；关闭后 `knowledge_closed`。服务绑定一个事件循环，SQLite 工作在线程中执行，连接按操作关闭。默认超时 30 秒，覆盖授权与索引工作；支持 `asyncio` 取消及带 `cancelled` 属性的 token。取消／超时／关闭会中断 SQLite 并等待工作线程清理，未提交事务回滚。提交边界上可能已经完成原子写入，调用方重试相同来源是幂等的。授权回调须合作响应取消，不得阻塞事件循环。

稳定错误还包括 `invalid_source`、`empty_source`、`duplicate_source`、`index_limit_exceeded`、`invalid_scope`、`invalid_query`、`query_limit_exceeded`、`invalid_limit`、`access_denied`、`citation_unavailable`、`knowledge_timeout`、`index_busy`、`index_unavailable`、`index_format_mismatch`。使用更低输出上限重新打开索引后，取回过大片段会报 `output_limit_exceeded`，可重新导入以应用更小分块。原始 SQLite、文件及授权异常不会返回给模型。首次初始化会验证数据库格式，不接管其他应用的数据库。

## 验证

```sh
python -m pytest plugins/knowledge_retrieval/tests -q
python -m ruff check plugins/knowledge_retrieval
python -m ruff format --check plugins/knowledge_retrieval/src plugins/knowledge_retrieval/tests plugins/knowledge_retrieval/examples
python -m build plugins/knowledge_retrieval
python scripts/check_repository.py
```

服务测试使用真实 SQLite。集成测试使用真实 Penhin 内核和确定性 Provider，不调用付费模型；未安装内核时显式跳过。跨插件合同测试另需安装当前 ASR 和媒体插件。尚未完成大规模语料的质量／性能评测及宿主引用 UI 验收。

实现参考：[SQLite FTS5 与 BM25](https://www.sqlite.org/fts5.html)、[Python sqlite3 事务及进度回调](https://docs.python.org/3.12/library/sqlite3.html)。
