# 大 PDF 处理设计方案

## 问题定义

学术论文 PDF 存在两类"大"：

| 类型 | 典型场景 | 数量级 |
|------|----------|--------|
| **页数多** | 综述论文、博士论文、会议论文集 | 20~200 页 |
| **文件大** | 图多（版图、波形、架构图）、彩色印刷 | 5~50 MB |

两类问题导致不同瓶颈：

```
页数多  →  get_pdf_layout_text 每页一次工具调用，全文需几十次
文件大  →  PyMuPDF 解析慢；图片块被跳过但占大量文件体积
```

当前 `extract_page_text` 只处理单页，Claude 需要循环调用——对 20 页论文就是 20 次 MCP 调用，context 里堆满坐标 JSON。

---

## 根本原因分析

### 真正的瓶颈：坐标 JSON 太啰嗦

每行文本需要存储 text + 4 个浮点数坐标，一页 ~60 行：

```json
{"text": "A 22nm Variation-Tolerant Clock...", "rect": [36.018, 752.451, 556.234, 764.907]}
```

一页约 **3~5 KB JSON**，20 页论文 = **60~100 KB** 纯坐标数据进 context。
这才是上下文爆炸的根本原因，不是文本本身。

### 图片不是问题（已跳过）

当前代码 `if block["type"] != 0: continue` 已跳过图片块。
PDF 文件大 ≠ context 大，PyMuPDF 只提取文字，图片不进 context。

---

## 解决方案矩阵

| 方案 | 解决什么 | 代价 | 推荐度 |
|------|----------|------|--------|
| **A. 服务端批量提取** | 减少工具调用次数 | 改 MCP tool | ⭐⭐⭐⭐⭐ |
| **B. 坐标懒加载** | 减少 context 占用 | 改 API 设计 | ⭐⭐⭐⭐⭐ |
| **C. 文本摘要模式** | 只传文字不传坐标 | 新增工具 | ⭐⭐⭐⭐ |
| **D. 语义分段** | 智能决定处理哪些页 | 需 embedding | ⭐⭐⭐ |
| E. 压缩图片质量 | 减小文件大小 | 损失质量 | ⭐⭐（治标） |
| F. 裁剪参考文献 | 减少页数 | 需识别页码 | ⭐⭐（治标） |
| G. 拆分 PDF | 分段处理 | 用户感知复杂 | ⭐（体验差） |
| H. 多 Agent 并发 | 加速处理 | 并发 ≤3 限制 | ⭐⭐（受限） |

**推荐优先实现 A + B，组合使用。**

---

## 方案 A：服务端批量提取（最高优先级）

### 新增工具：`get_pdf_text_bulk`

```python
@mcp.tool()
def get_pdf_text_bulk(
    item_id: str,
    pages: list[int] | None = None,   # None = 全文
    skip_refs: bool = True,            # 自动跳过参考文献页
) -> str:
    """批量提取多页 PDF 文本，一次调用返回所有页。
    
    相比 get_pdf_layout_text 逐页调用，减少 80% 的工具调用次数。
    返回格式：每页一个对象，包含页码、文本内容、总行数。
    坐标不包含在此接口中——如需精确标注坐标，再用 get_pdf_layout_text。
    """
```

**核心思路**：把"提取文本供 LLM 理解"和"获取坐标供标注写回"分离成两个工具：

```
步骤 1: get_pdf_text_bulk(全文)   → LLM 理解内容，找到目标句子
步骤 2: get_pdf_layout_text(N页)  → 只对目标页获取精确坐标
步骤 3: create_pdf_annotation     → 写入标注
```

这样 context 里只有：全文文字 + 1~2 页坐标，而不是全文坐标。

### 实现要点

```python
def extract_bulk_text(
    pdf_path: str | Path,
    pages: list[int] | None = None,
    skip_refs: bool = True,
) -> dict:
    doc = fitz.open(str(pdf_path))
    total_pages = len(doc)
    
    # 自动检测参考文献起始页
    refs_start = _detect_refs_page(doc) if skip_refs else total_pages
    
    target_pages = pages or list(range(min(refs_start, total_pages)))
    
    result = {"total_pages": total_pages, "pages": []}
    for pn in target_pages:
        page = doc[pn]
        # 只提取纯文本，不带坐标（节省 80% 体积）
        text = page.get_text("text")
        result["pages"].append({
            "page": pn,
            "text": text.strip(),
            "char_count": len(text),
        })
    
    return result


def _detect_refs_page(doc) -> int:
    """启发式检测参考文献起始页（从后往前找 References/Bibliography 标题）。"""
    for i in range(len(doc) - 1, max(0, len(doc) - 10), -1):
        text = doc[i].get_text("text").lower()
        if any(kw in text for kw in ["references\n", "bibliography\n", "参考文献\n"]):
            return i
    return len(doc)  # 未检测到，返回全文
```

---

## 方案 B：坐标懒加载（两阶段工作流）

### 设计原则

> **文本理解阶段不需要坐标，标注写入阶段才需要坐标。**

当前工具将两者混在一起（`get_pdf_layout_text` 每次都返回坐标），导致理解阶段浪费大量 context。

### 新工作流（对 Claude 的 prompt 约束）

```
Phase 1 - 理解（轻量）：
  → get_pdf_text_bulk(item_id)        # 返回纯文本，无坐标
  → LLM 分析：哪些句子需要高亮？在第几页？
  → 输出：{ "page": 0, "target_text": "..." } 列表

Phase 2 - 定位（精准）：
  → get_pdf_layout_text(item_id, page=0)  # 只查目标页
  → 在该页坐标数据中匹配 target_text
  → create_pdf_annotation(rects=...)
```

**Context 节省估算：**
- 旧流程（20页论文）：20 × 5KB = 100KB 坐标
- 新流程：全文纯文本 ~20KB + 2页坐标 ~10KB = **70% 节省**

---

## 方案 C：文本摘要模式（针对综述/长文）

对于只需要"读懂然后写笔记"而不需要精确标注的场景，直接提取纯文本摘要：

```python
@mcp.tool()
def get_pdf_summary_text(
    item_id: str,
    sections: list[str] | None = None,  # ["abstract", "introduction", "conclusion"]
) -> str:
    """提取 PDF 关键章节的纯文本（无坐标），适合大文件的内容理解。
    
    sections 支持：abstract、introduction、conclusion、所有标题匹配
    不传 sections 时默认提取摘要+引言+结论。
    """
```

---

## 实现优先级

### MVP 改动（现在做）

```
1. pdf_tools.py
   + extract_bulk_text(pages=None, skip_refs=True)
   + _detect_refs_page(doc) 启发式检测参考文献页

2. server.py  
   + get_pdf_text_bulk tool（对外接口）
   
3. 修改 MCP server instructions（system prompt）
   强制约束 Claude 使用两阶段工作流：
   "处理大 PDF 时，先用 get_pdf_text_bulk 理解内容，
    再用 get_pdf_layout_text 获取目标页坐标，
    不要对每一页都调用 get_pdf_layout_text"
```

### Phase 2（做 Skill 时）

```
4. get_pdf_summary_text（章节感知提取）
5. Skill prompt 模板内置两阶段流程约束
6. 自动分块：超过 50 页论文自动按章节分段处理
```

---

## 文件大小限制参考

| 场景 | 文件大小 | 页数 | 推荐策略 |
|------|----------|------|----------|
| 会议短文 | <2MB | 6~12 页 | 直接 get_pdf_layout_text 逐页处理 |
| 期刊长文 | 2~10MB | 15~30 页 | get_pdf_text_bulk + 目标页坐标 |
| 综述/博士论文 | >10MB | 50~200 页 | get_pdf_summary_text + 章节拆分 |
| 会议论文集 | >50MB | 500+ 页 | 先拆分单篇再处理（Zotero 侧操作） |

---

## 对比总结

| | 旧方式 | 新方式（A+B） |
|--|--------|---------------|
| 工具调用次数（20页论文） | ~20 次 | ~3 次 |
| Context 占用 | ~100KB 坐标 | ~30KB |
| 处理速度 | 慢（每页等待） | 快（批量返回） |
| 参考文献处理 | 包含（浪费） | 自动跳过 |
| 精度 | 高（每页坐标） | 同等（两阶段） |
