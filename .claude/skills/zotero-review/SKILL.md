---
name: zotero-review
description: >
  以学术审稿人视角审阅论文，输出结构化评审意见。
  可指定会议级别（MICRO/ISCA/HPCA/ASPLOS/DAC/ISSCC 等），
  输出包括分维度评分、优缺点分析、逐条修改建议。
  触发词：review paper、审阅论文、paper review、审稿、评审
when_to_use: >
  用户要求审阅论文、写 review、模拟审稿意见、检查论文质量。
argument-hint: "[PDF路径或论文标题] [会议名称(可选)]"
user-invocable: true
disable-model-invocation: false
effort: high
---

# Zotero 论文审阅

以顶会审稿人视角审阅一篇学术论文，输出结构化评审报告。

## 输入解析

用户输入：`$ARGUMENTS`

提取：
1. **目标论文**：PDF 路径或标题
2. **目标会议**（可选）：如 MICRO、ISCA、HPCA、ASPLOS、DAC 等。默认按通用学术标准。

## 工作流程

### Step 1：获取论文

用 `search_zotero_items` 或直接路径定位 PDF。
用 `get_item_metadata` 获取元数据。

### Step 2：提取全文

调用 `get_pdf_text_bulk(item_id, skip_refs=True)` 提取正文。

### Step 3：深度审阅

仔细阅读全文，从以下维度评估：

#### 评分维度（1-5 分）

| 维度 | 评估标准 |
|------|----------|
| Novelty (新颖性) | 研究问题和解决方案的原创性 |
| Technical Quality (技术质量) | 方法的正确性、严谨性、完整性 |
| Significance (重要性) | 对领域的潜在影响和实用价值 |
| Clarity (表达清晰度) | 写作质量、图表质量、逻辑连贯性 |
| Experimental Evaluation (实验评估) | 实验设计、baseline 选择、结果可信度 |

### Step 4：生成审阅报告

按以下格式输出：

```
## 审阅报告

### 论文信息
- 标题：{title}
- 作者：{authors}
- 目标会议：{venue}

### 总体评价
Overall Score: X/5
Recommendation: [Strong Accept / Accept / Weak Accept / Borderline / Weak Reject / Reject]

一段话总结论文核心贡献和主要问题。

### 分维度评分
- Novelty: X/5 — 简要说明
- Technical Quality: X/5 — 简要说明
- Significance: X/5 — 简要说明
- Clarity: X/5 — 简要说明
- Experimental Evaluation: X/5 — 简要说明

### 优点 (Strengths)
1. [S1] ...
2. [S2] ...
3. [S3] ...

### 缺点 (Weaknesses)
1. [W1] ...
2. [W2] ...
3. [W3] ...

### 详细意见 (Detailed Comments)

逐节给出具体修改建议，包括：
- 引言部分：motivation 是否清晰？
- 方法部分：技术方案是否完整？有无漏洞？
- 实验部分：baseline 是否充分？实验设计是否合理？
- 写作：是否有语法错误、表述不清的地方？

### 小问题 (Minor Issues)
- 具体页码和行的小问题列表

### 给作者的建议
如果要修改重投，最应该改进的 3 个方面。
```

### Step 5：可选 — 保存为笔记

询问用户是否要将审阅报告保存为 Zotero 子笔记。
如果是，用 `add_child_note` 保存（HTML 格式）。

### Step 6：可选 — 标注关键问题

询问用户是否要在 PDF 中标注发现的问题：
- 技术问题标红色 `#ff6666`
- 写作问题标黄色 `#ffd400`
- 亮点标绿色 `#28CA42`

如果是，执行 `/zotero-annotate` 的两阶段流程。

## 审阅原则

- **建设性**：指出问题的同时给出改进方向
- **具体**：不说"实验不够充分"，而说"缺少与 XXX 的对比"
- **公正**：不因写作风格或语言问题过度扣分
- **专业**：评价基于技术内容，不涉及个人偏好
