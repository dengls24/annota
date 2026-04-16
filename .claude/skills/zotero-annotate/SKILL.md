---
name: zotero-annotate
description: >
  Zotero 论文智能标注。使用两阶段工作流：先提取全文理解内容，再对目标句子精确标注。
  支持按语义分类标注（结果=绿色、方法=蓝色、不足=红色、贡献=紫色）。
  触发词：annotate、highlight、标注、高亮、mark this paper、标出论文中的
when_to_use: >
  用户要求标注论文中的特定内容（发现、方法、贡献、不足等），
  或要求对 Zotero 中的 PDF 做高亮标注，或给出 PDF 路径/论文标题要求标注。
argument-hint: "[PDF路径或论文标题] [标注内容描述]"
user-invocable: true
disable-model-invocation: false
---

# Zotero 论文智能标注

你将对一篇学术论文 PDF 执行智能标注。严格遵循两阶段工作流。

## 输入解析

用户输入：`$ARGUMENTS`

从输入中提取：
1. **目标 PDF**：文件路径、论文标题、或 Zotero item key
2. **标注内容**：要标注什么（如"发现结果"、"方法"、"limitation"等）
3. **标注颜色**：如果用户指定了颜色则使用，否则按语义自动选择

## 颜色语义映射

| 内容类型 | 颜色 | 代码 |
|----------|------|------|
| 实验结果、发现、数据、性能 | 绿色 | #28CA42 |
| 方法、算法、模型、架构设计 | 蓝色 | #2EA8E5 |
| 问题、不足、limitation、future work | 红色 | #ff6666 |
| 创新点、贡献、novelty | 紫色 | #a28ae5 |
| 通用/用户未指定 | 黄色 | #ffd400 |

## Phase 1 — 理解论文内容

### Step 1.1：定位 PDF

如果用户提供了文件路径，直接使用。否则：
- 调用 `search_zotero_items(query)` 搜索论文
- 获取 `pdf_attachment_id` 或从路径中提取信息

### Step 1.2：提取全文

调用 `get_pdf_text_bulk(item_id, skip_refs=True)` 批量提取纯文本。

这个工具不返回坐标，体积小，适合全文理解。

### Step 1.3：分析内容

仔细阅读提取的文本，根据用户指定的标注目标，识别出所有需要标注的句子：
- 记录每个目标句子的**完整文本**
- 记录它在**哪一页**（page 字段）
- 记录它属于**哪种语义类型**（结果/方法/不足/贡献）

输出一个清单：
```
Page 0: "the DCC demonstrates significant improvements..." → 绿色(结果)
Page 0: "maintaining a rapid response time of 51 cycles" → 绿色(结果)
```

### Step 1.4：检查已有标注

调用 `list_annotations(item_id)` 查看是否已有标注，避免重复。

## Phase 2 — 精确标注

### Step 2.1：获取目标页坐标

只对包含目标句子的页面调用 `get_pdf_layout_text(item_id, page_number)`。

**绝对不要**对每一页都调用这个工具——只调用目标页。

### Step 2.2：匹配坐标

在返回的 blocks 中找到目标句子对应的 rect 坐标。
一个句子可能跨多行，需要收集所有匹配行的 rect。

匹配时用子串匹配（句子的前 30 个字符即可定位），而不是精确匹配。

### Step 2.3：写入标注

**提醒用户**：写入标注需要关闭 Zotero 桌面应用。如果用户确认已关闭，继续。

如果只有 1-2 条标注，用 `create_pdf_annotation`。
如果有 3 条以上，用 `batch_annotate` 一次性写入：

```json
{
  "item_id": "...",
  "annotations": [
    {"page_index": 0, "rects": [...], "color": "#28CA42", "text": "..."},
    {"page_index": 0, "rects": [...], "color": "#2EA8E5", "text": "..."}
  ]
}
```

### Step 2.4：确认结果

告诉用户：
1. 创建了多少条标注
2. 每条标注的位置和颜色
3. 提醒重新打开 Zotero 并按 `Ctrl+Shift+R` 刷新

## 错误处理

- 如果 `batch_annotate` 返回 `database_locked` 错误，提醒用户关闭 Zotero 后重试
- 如果找不到目标句子的坐标，报告哪些句子未能匹配，给出可能原因（OCR 误差、特殊字符等）
