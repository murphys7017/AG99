# Memory Document Search

本文件定义 AstrBot memory 系统中的“文档搜索”部分。

目标：

- 明确 `LongTermMemory` 的设计思想
- 明确文档搜索在 memory 系统中的职责边界
- 明确第一版搜索对象、索引对象与回表对象
- 明确后续 `vector_index.py` / `document_search.py` 的实现方向

## 1. 先重申 `LongTermMemory` 的设计思想

在开始设计文档搜索之前，必须先明确我们对 `LongTermMemory` 的共识。

### 1.1 `LongTermMemory` 不是对话存档

`LongTermMemory` 的目标不是：

- 保存所有历史对话
- 替代 `TurnRecord`
- 替代 `Experience`

它的目标是：

- 沉淀高价值、可持续更新的长期认知对象

也就是说：

- `TurnRecord` 是原始回合材料
- `Experience` 是时间线事件流
- `LongTermMemory` 是从一组相关 `Experience` 中生长出来的稳定认知对象

### 1.2 `LongTermMemory` 不是一次性摘要

`LongTermMemory` 不是“某次总结的结果快照”，而是：

- 可被后续事件继续补充
- 可被后续证据修正
- 可被标记为失效、冲突或归档

所以长期记忆不是静态文档，而是：

- 有状态
- 有时间跨度
- 有来源引用
- 可演进

### 1.3 `LongTermMemory` 的核心单位是“记忆对象”

长期记忆层的核心对象不是：

- 文档块
- chunk
- 上传文件

而是：

- 一条长期认知对象

这一对象可能表示：

- 用户的稳定偏好
- 持续推进的项目判断
- 长期有效的事实认知
- 某段关系变化中形成的稳定认识

因此：

- `LongTermMemory` 是 memory-object centric
- AstrBot 现有 Knowledge Base 是 document/chunk centric

这也是为什么 memory 可以复用 RAG 的底层能力，但不能直接复用 KB 的对象模型。

### 1.4 `LongTermMemory` 的主存储是真源索引加正文文档

当前共识：

- `SQLite` 保存结构化索引与元数据
- `Markdown` 保存正文与可审阅表达
- 向量索引只负责召回，不是真源

因此：

- 数据正确性以 `SQLite + Markdown` 为准
- 检索只负责“找回来”，不负责定义记忆事实

### 1.5 `LongTermMemory` 与 `Experience` 的关系

当前共识不是在 `Experience` 层做强归并，而是：

- `Experience` 保持事件流属性
- `LongTermMemory` 负责对相关 `Experience` 做稳定沉淀

可理解为：

`Experience = 证据流`

`LongTermMemory = 被证据持续支撑或更新的认知对象`

所以长期记忆的关键能力不是“存储”，而是：

- 归并
- 更新
- 修正
- 检索

## 2. 文档搜索的定位

本项目中的“文档搜索”不是泛化的全局 RAG，而是：

- 面向 `LongTermMemory` 文档对象的检索基础设施

第一版文档搜索只负责：

- 搜索长期记忆文档
- 返回候选长期记忆对象
- 支持按 scope 回表与按需加载正文

第一版文档搜索不负责：

- prompt 注入
- prompt 拼接
- chat state
- intent router
- `Experience` 与 `LongTermMemory` 混合检索
- 通用知识库上传 / 分块管理

一句话定义：

`Document Search = 给定 query，在长期记忆文档中找出相关 memory objects 的系统`

## 3. 为什么先做文档搜索

长期记忆要真正可用，不只是“有文档”，而是必须能被稳定找到。

后续这些模块都会依赖同一个基础能力：

- `Prompt Collector`
- Prompt target projection / retrieval policy
- 长期记忆召回
- 人格推理支撑材料加载

它们在本质上都依赖：

`query -> 候选长期记忆 -> 回表 -> 加载正文`

所以文档搜索是长期记忆读取链路的基础设施，而不是附属功能。

## 4. 第一版搜索对象范围

第一版明确只搜索：

- `LongTermMemory`

第一版明确不直接搜索：

- `TurnRecord`
- `TopicState`
- `ShortTermMemory`
- `SessionInsight`
- `Experience`
- `PersonaState`

原因：

- `TurnRecord` 过于原始
- 短期层属于高频状态，不应文档化搜索
- `Experience` 是中间事件流，适合作为长期记忆的证据，不适合作为第一版主搜索对象
- 人格层后续有自己更专门的读取策略

第一版这样收窄后，文档搜索的职责会非常清楚：

- 只对长期记忆文档负责

## 5. 搜索分层模型

建议把文档搜索拆成 3 个对象层。

### 5.1 `LongTermMemoryIndex`

职责：

- 结构化索引
- scope 过滤
- 元数据回表
- 指向正文文档路径

主存储：

- `SQLite`

建议字段：

- `memory_id`
- `umo`
- `scope_type`
- `scope_id`
- `category`
- `title`
- `summary`
- `status`
- `importance`
- `confidence`
- `tags`
- `doc_path`
- `source_refs`
- `first_event_at`
- `last_event_at`
- `created_at`
- `updated_at`

说明：

- 当前仓库已有 `LongTermMemoryIndex` 雏形
- 后续应扩到能支撑真正长期记忆搜索

### 5.2 `LongTermMemoryDocument`

职责：

- 保存长期记忆正文
- 给人工审阅与模型精读使用

主存储：

- `Markdown`

建议路径：

- `data/memory/long_term/<umo>/<scope_type>/<memory_id>.md`

说明：

- 文档是长期记忆的正文表达
- 它不直接承担高频过滤与排序职责

### 5.3 `DocumentSearchEntry`

职责：

- 作为向量索引中的搜索条目
- 保存用于 embedding 的标准化文本与 metadata

主存储：

- 向量索引

说明：

- 这个对象不是 `Markdown` 原文本身
- 也不是数据库全量对象原样复制
- 它是面向检索优化后的搜索表达

## 6. 搜索文本设计

第一版不建议把整个 Markdown 原文直接写入 embedding。

原因：

- 正文里可能有大量对搜索不友好的结构信息
- 文档会包含审阅信息、source refs、更新日志等噪声
- 直接塞全文会让检索目标不稳定

建议引入标准化的 `search_text`：

```text
Title: ...
Category: ...
Summary: ...
Detail: ...
Tags: tag1, tag2, tag3
Recent Updates: ...
```

建议来源：

- `title`
- `summary`
- 正文中的核心理解段
- 最近更新摘要
- `tags`

不建议直接写入搜索文本的内容：

- 原始 YAML 全字段
- 全量 `source_refs`
- 很长的证据清单
- 整个 Markdown 原文不加处理直接拼接

文档搜索的稳定性，很大程度上取决于：

- 搜索文本是否结构化且可控

## 7. 检索链路

第一版建议固定为 4 个步骤。

### 7.1 Scope Filter

先做范围收窄：

- `umo`
- `scope_type`
- `scope_id`

后续可选过滤：

- `category`
- `status`
- `tags`

说明：

- 文档搜索首先是“在谁的长期记忆里搜”
- 然后才是“搜什么”

### 7.2 Candidate Retrieval

在候选范围内执行向量检索。

第一版建议：

- 先做 dense retrieval

后续可选：

- sparse retrieval
- hybrid retrieval
- rerank

### 7.3 Hydration

向量结果只返回候选标识与分数。

然后：

- 通过 `memory_id` 回表查 `LongTermMemoryIndex`
- 按需读取 `Markdown` 正文

说明：

- 检索结果不能直接等于最终输出
- 最终输出必须基于真源对象回表得到

### 7.4 Post Rank

第一版排序策略建议从简：

- 先按向量相似度排序
- 同分按 `importance DESC`
- 再按 `updated_at DESC`

后续可升级为加权排序：

- `vector_score`
- `importance`
- `confidence`
- `freshness`

## 8. 复用 AstrBot 现有 RAG 的方式

当前仓库中已经存在知识库 / RAG 栈。

适合复用的部分：

- `FaissVecDB`
- embedding provider 接法
- rerank provider 接法
- dense / sparse / rerank 的编排思路

不建议直接复用的部分：

- `KnowledgeBaseManager`
- `KBHelper`
- 文档上传 / 分块生命周期
- 知识库的 `kb -> document -> chunk` 对象模型

原因：

- 知识库是文档导向
- memory 长期记忆是记忆对象导向

因此正确姿势应是：

- 复用底层向量与 provider 能力
- 自己实现 memory 专用的 document search 层

## 9. 推荐模块结构

建议新增以下模块：

- `astrbot/core/memory/document_loader.py`
- `astrbot/core/memory/document_serializer.py`
- `astrbot/core/memory/vector_index.py`
- `astrbot/core/memory/document_search.py`

### 9.1 `document_loader.py`

职责：

- 读取长期记忆 Markdown 文档
- 解析 front matter
- 返回结构化文档对象

建议函数：

```python
async def load_long_term_document(self, doc_path: Path) -> LongTermMemoryDocument: ...
async def save_long_term_document(self, document: LongTermMemoryDocument) -> Path: ...
```

### 9.2 `document_serializer.py`

职责：

- 把长期记忆索引对象与正文对象转换成 `search_text`
- 保证 embedding 输入稳定

建议函数：

```python
def build_search_text(
    index: LongTermMemoryIndex,
    document: LongTermMemoryDocument | None = None,
) -> str: ...
```

### 9.3 `vector_index.py`

职责：

- 管理长期记忆的向量索引
- 负责 upsert / delete / search

建议函数：

```python
async def upsert_long_term_memory(self, memory_id: str) -> None: ...
async def delete_long_term_memory(self, memory_id: str) -> None: ...
async def search_long_term_memories(
    self,
    umo: str,
    query: str,
    top_k: int,
    metadata_filters: dict | None = None,
) -> list[VectorSearchHit]: ...
```

### 9.4 `document_search.py`

职责：

- 承接搜索请求
- 做 scope 过滤
- 调用向量索引
- 回表并按需加载正文
- 返回稳定结果对象

建议函数：

```python
async def search_long_term_memories(
    self,
    req: DocumentSearchRequest,
) -> list[DocumentSearchResult]: ...
```

## 10. 建议数据类型

### 10.1 `DocumentSearchRequest`

建议结构：

```python
@dataclass(slots=True)
class DocumentSearchRequest:
    umo: str
    query: str
    conversation_id: str | None = None
    scope_type: str | None = None
    scope_id: str | None = None
    category: str | None = None
    top_k: int = 5
    include_body: bool = False
```

### 10.2 `DocumentSearchResult`

建议结构：

```python
@dataclass(slots=True)
class DocumentSearchResult:
    memory_id: str
    score: float
    title: str
    summary: str
    category: str
    tags: list[str]
    doc_path: str
    body_text: str | None = None
```

### 10.3 `VectorSearchHit`

建议结构：

```python
@dataclass(slots=True)
class VectorSearchHit:
    memory_id: str
    score: float
    metadata: dict[str, Any]
```

## 11. Metadata 设计

每条向量索引 entry 至少应保存：

- `memory_id`
- `umo`
- `scope_type`
- `scope_id`
- `category`
- `status`
- `tags`

作用：

- 做检索前过滤
- 做回表定位
- 为后续混合检索和 rerank 留接口

## 12. 第一版不做什么

第一版文档搜索明确不做：

- 搜索 `Experience`
- 搜索所有 Markdown 文件
- 直接把整份文档切 chunk 后纳入 KB 生命周期
- prompt 注入
- collector 接入
- query-aware snapshot 扩张
- 通用知识库能力抽象

第一版的完成标准应是：

- 能对长期记忆文档稳定建索引
- 能按 `umo + scope` 执行搜索
- 能回表得到结构化长期记忆对象
- 能按需加载正文

## 13. 推荐实现顺序

建议顺序：

1. 扩充 `LongTermMemoryIndex` 数据模型
2. 定义长期记忆 Markdown 正文结构
3. 实现 `document_loader.py`
4. 实现 `document_serializer.py`
5. 实现 `vector_index.py`
6. 实现 `document_search.py`
7. 再由外部模块消费搜索结果

说明：

- 先把长期记忆对象定义稳定
- 再做搜索
- 不要先做 prompt 集成

## 14. 当前结论

当前对文档搜索的共识可以收敛为：

- 搜索对象只限定为 `LongTermMemory`
- `SQLite` 与 `Markdown` 是真源
- 向量索引只是召回层
- 复用 AstrBot 的底层向量 / provider 能力，但不直接复用知识库对象模型
- 文档搜索的本质是：

`query -> candidate memories -> 回表 -> 按需加载正文`

这将作为后续长期记忆读取、prompt collector 消费和更复杂 retrieval 的基础设施。
