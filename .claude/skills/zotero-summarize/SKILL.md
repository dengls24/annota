---
name: zotero-summarize
description: >
  为 Zotero 论文生成结构化阅读笔记，自动保存为 Zotero 子笔记。
  提取核心贡献、方法、实验结果、不足和启发，生成中英文双语笔记。
  触发词：summarize paper、论文笔记、读书笔记、paper note、总结这篇论文
when_to_use: >
  用户要求对论文写笔记、总结、摘要，或要求生成结构化阅读笔记。
argument-hint: "[PDF路径或论文标题]"
user-invocable: true
disable-model-invocation: false
---

# Zotero 论文结构化笔记

为一篇学术论文生成结构化阅读笔记，并保存到 Zotero。

## 输入解析

用户输入：`$ARGUMENTS`

## 工作流程

### Step 1：定位论文

如果用户提供了文件路径，直接使用。否则用 `search_zotero_items` 搜索。

### Step 2：获取元数据

调用 `get_item_metadata(item_id)` 获取：
- 标题、作者、年份、期刊/会议、DOI

### Step 3：提取全文

调用 `get_pdf_text_bulk(item_id, skip_refs=True)` 提取论文正文。

### Step 4：生成结构化笔记

仔细阅读全文，按以下模板生成笔记：

```html
<h2>📄 论文信息</h2>
<p><b>标题：</b>{title}</p>
<p><b>作者：</b>{authors}</p>
<p><b>发表：</b>{venue} {year}</p>
<p><b>DOI：</b>{doi}</p>

<h2>🎯 核心问题</h2>
<p>这篇论文要解决什么问题？为什么重要？</p>

<h2>💡 核心贡献</h2>
<ol>
<li>贡献 1</li>
<li>贡献 2</li>
<li>贡献 3</li>
</ol>

<h2>🔧 方法</h2>
<p>提出了什么方法/架构/算法？关键设计决策是什么？</p>

<h2>📊 实验结果</h2>
<ul>
<li>关键指标和数据</li>
<li>与 baseline 的对比</li>
</ul>

<h2>⚠️ 局限性</h2>
<ul>
<li>论文自述的 limitation</li>
<li>你认为的潜在不足</li>
</ul>

<h2>💭 个人启发</h2>
<p>这篇论文对我的研究有什么启发？可以借鉴什么？</p>

<h2>🔗 相关工作</h2>
<p>值得进一步阅读的参考文献</p>
```

### Step 5：保存到 Zotero

**提醒用户**：保存笔记需要关闭 Zotero。

调用 `add_child_note(parent_item_id, note_content)` 将笔记保存为论文的子笔记。

`parent_item_id` 是文献条目的 ID（不是 PDF 附件的 ID）。可以从 `get_item_metadata` 返回的 `itemID` 获取。

### Step 6：确认

告诉用户笔记已保存，提醒打开 Zotero 按 `Ctrl+Shift+R` 刷新查看。

## 注意事项

- 笔记内容使用 HTML 格式（Zotero 原生支持）
- 每个要点简洁有力，避免复制粘贴原文大段内容
- "个人启发"部分可以根据用户的研究方向定制（如果已知）
- 如果论文是中文的，笔记用中文；英文论文默认中文笔记，关键术语保留英文
