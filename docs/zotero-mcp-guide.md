# Zotero MCP 使用指南

## 工具概览

zotero-mcp 是一个本地 MCP Server，让 Claude Code 能直接操作 Zotero 库中的 PDF：提取文本、创建高亮标注、添加笔记。

### 可用工具

| 工具 | 功能 | Zotero 运行时可用？ |
|------|------|:-------------------:|
| `search_zotero_items` | 按标题/作者/key 搜索条目 | ✅ |
| `list_zotero_items` | 列出最近条目 | ✅ |
| `get_item_metadata` | 获取条目元数据（作者、年份、期刊、DOI） | ✅ |
| `get_pdf_text_bulk` | 批量提取多页纯文本（无坐标，大 PDF 专用） | ✅ |
| `get_pdf_layout_text` | 提取单页文本及精确坐标 | ✅ |
| `list_annotations` | 列出 PDF 上已有的标注 | ✅ |
| `create_pdf_annotation` | 创建单条高亮/下划线标注 | ❌ 需关闭 Zotero |
| `batch_annotate` | 一次性创建多条标注 | ❌ 需关闭 Zotero |
| `add_child_note` | 为条目添加子笔记 | ❌ 需关闭 Zotero |

---

## 如何使用

### 第一步：给 Claude 提供论文信息

**最简单的方式**：在 Zotero 中右键 PDF 附件 → "Show File"，复制文件路径，直接粘给 Claude。

路径格式类似：
```
E:\asic-soc\0-文献&翻译\Zotero文献\storage\4SQST7E8\论文名.pdf
```

也可以提供：
- 论文标题或作者（Claude 会用 `search_zotero_items` 查找）
- Zotero item key（路径中 `storage/` 后面的 8 位字符，如 `4SQST7E8`）

### 第二步：告诉 Claude 要做什么

**标注指令模板：**
```
用Zotero把这篇论文[摘要/第X页]中的[XXX内容]用[颜色]标出来
"[PDF路径]"
```

**示例：**
- "用Zotero把这篇论文摘要中的发现结果用绿色标出来"
- "用Zotero在第3页给方法部分加蓝色高亮"
- "给这篇论文添加一条子笔记，总结核心贡献"
- "搜索 Zotero 里所有关于 Duty-Cycle 的论文"

### 第三步：写操作前关闭 Zotero

如果需要**创建标注或笔记**（写操作），必须先关闭 Zotero 桌面应用。

操作完成后重新打开 Zotero，按 `Ctrl+Shift+R` 刷新即可看到标注。

> 注意：搜索、列表、提取文本等**读操作**不需要关闭 Zotero。

---

## 颜色规范

| 颜色 | 代码 | 建议用途 |
|------|------|----------|
| 🟡 黄色 | `#ffd400` | 默认高亮 |
| 🟢 绿色 | `#28CA42` | 实验结果 / 发现 |
| 🔵 蓝色 | `#2EA8E5` | 方法 / 定义 |
| 🔴 红色 | `#ff6666` | 问题 / 不足 / limitation |
| 🟣 紫色 | `#a28ae5` | 创新点 / 贡献 |

---

## 环境配置

### MCP Server 配置（已配好）

在 `~/.claude.json` 的 `mcpServers` 中：

```json
"zotero-mcp": {
  "command": "E:/.../papernote/.venv/Scripts/python.exe",
  "args": ["E:/.../papernote/zotero_mcp/server.py"],
  "env": {
    "ZOTERO_DATA_DIR": "E:\\asic-soc\\0-文献&翻译\\Zotero文献"
  }
}
```

### 依赖

- Python 3.10+
- PyMuPDF (`pip install pymupdf`)
- MCP SDK (`pip install mcp`)

---

## 技术说明

### 为什么读操作不需要关闭 Zotero？

Zotero 7 使用 SQLite 排他锁模式（`EXCLUSIVE`），运行时外部无法直接读写数据库。
我们的解决方案：**读操作时复制数据库快照到临时目录再读取**（耗时 <100ms）。

### 为什么写操作还是需要关闭？

写入必须操作原始数据库文件。排他锁无法绕过，即使 SQLite WAL 模式也不行。
写操作内置了重试机制（3 次指数退避），但如果 Zotero 一直运行，最终仍会失败。

### 远期方案

开发 Zotero 插件桥接层（HTTP API），让写操作通过 Zotero 内部 API 执行，彻底消除锁冲突。

---

## 大 PDF 处理（两阶段工作流）

对于超过 10 页的论文，直接对每页调用 `get_pdf_layout_text` 会导致上下文溢出。
推荐使用**两阶段工作流**：

### Phase 1 — 理解内容

```
get_pdf_text_bulk(item_id, skip_refs=True)
```

批量提取全文纯文本（无坐标），让 AI 理解论文内容，确定哪些页面哪些句子需要标注。
自动跳过参考文献页以节省 context。

### Phase 2 — 精确标注

```
get_pdf_layout_text(item_id, page_number=目标页)
create_pdf_annotation(item_id, page_index=目标页, rects=目标坐标)
```

只对 Phase 1 确定的目标页获取精确坐标，然后写入标注。

### 效果对比

| | 旧方式（逐页） | 新方式（两阶段） |
|--|----------------|-----------------|
| 20 页论文工具调用次数 | ~20 次 | ~3 次 |
| Context 占用 | ~100KB | ~30KB |
| 参考文献处理 | 包含（浪费） | 自动跳过 |

### 不同大小论文的推荐策略

| 论文类型 | 页数 | 推荐工具 |
|----------|------|----------|
| 会议短文 | <12 页 | 直接 `get_pdf_layout_text` 逐页 |
| 期刊长文 | 15~30 页 | `get_pdf_text_bulk` + 目标页坐标 |
| 综述/博士论文 | 50+ 页 | `get_pdf_text_bulk(pages=[0,1,...])` 分批 |
