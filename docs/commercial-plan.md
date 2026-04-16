# Zotero AI Assistant — 商业化方案

## 产品定位

**Zotero AI Assistant** — 基于 LLM 的学术 PDF 智能标注与笔记助手

核心能力：
1. **智能标注**：AI 理解论文内容，自动/按指令标注关键发现、方法、不足等
2. **结构化笔记**：自动生成论文摘要、批判性笔记、对比表格
3. **批量处理**：一次标注整个 collection 中的所有论文
4. **自定义 Prompt**：用户定义标注规则（如 "标绿色=结果, 蓝色=方法"）

---

## 技术架构

### 当前架构（MVP，已实现）

```
Claude Code / Cursor
        │ MCP Protocol (stdio)
        ▼
zotero-mcp Server (Python)
├── pdf_tools    — PyMuPDF 提取文本+坐标
├── zotero_db    — SQLite 读写（复制快照读 + 重试写）
└── server.py    — 5 个 MCP tool
        │
        ▼
Zotero SQLite DB ← Zotero 桌面应用
```

**当前限制**：写操作需关闭 Zotero（排他锁）

### 商业版架构（目标）

```
┌──────────────────────────────────────────────────────────┐
│                       用户端                              │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │ Zotero 插件   │  │ Claude Skill │  │ Web Dashboard │  │
│  │(一键标注按钮) │  │ (MCP Server) │  │ (管理/配置)   │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬────────┘  │
├─────────┼─────────────────┼─────────────────┼────────────┤
│         ▼                 ▼                 ▼            │
│  ┌───────────────────────────────────────────────┐       │
│  │           API Gateway (认证 + 计费)            │       │
│  │      JWT Auth / API Key / Usage Metering       │       │
│  └────────────────────┬──────────────────────────┘       │
│                       ▼                                   │
│  ┌───────────────────────────────────────────────┐       │
│  │           Core Service (Python/FastAPI)         │       │
│  │  ┌──────────┐ ┌──────────┐ ┌───────────────┐  │       │
│  │  │ PDF 解析  │ │ LLM 调度 │ │ Prompt 引擎   │  │       │
│  │  │(PyMuPDF) │ │(Claude)  │ │(模板+自定义)  │  │       │
│  │  └──────────┘ └──────────┘ └───────────────┘  │       │
│  └────────────────────┬──────────────────────────┘       │
│                       ▼                                   │
│  ┌──────────┐ ┌──────────────┐ ┌──────────────────┐     │
│  │ Zotero   │ │  用户 DB     │ │  Prompt Store    │     │
│  │ Web API  │ │ (PostgreSQL) │ │  (模板市场)      │     │
│  └──────────┘ └──────────────┘ └──────────────────┘     │
└──────────────────────────────────────────────────────────┘
```

**关键升级路径**：
1. Zotero 插件桥接（HTTP API）→ 彻底解决写操作锁冲突
2. Zotero Web API 对接 → 支持云端库操作
3. FastAPI 后端 → 支持多用户、计费、Prompt 市场

---

## 产品分层 (SKU)

| 层级 | 名称 | 价格 | 能力 |
|------|------|------|------|
| Free | 体验版 | 免费 | 5 次/月标注, 单页处理, 基础 prompt |
| Pro | 专业版 | ¥49/月 | 无限标注, 全文处理, 自定义 prompt, 批量操作 |
| Team | 团队版 | ¥199/月/5人 | Pro + 共享 prompt 库 + 团队标注规范 |
| API | 开发者版 | ¥0.1/次 | REST API 按调用计费, 可集成到第三方工具 |

---

## Skill 库设计

### 内置 Skill

| Skill | 功能 | 调用示例 |
|-------|------|---------|
| `annotate-findings` | 标注实验结果 (绿色) | "标出这篇论文的所有实验结果" |
| `annotate-methods` | 标注方法论 (蓝色) | "标出核心算法和实验设计" |
| `annotate-gaps` | 标注研究不足 (红色) | "标出 limitations 和 future work" |
| `annotate-contributions` | 标注创新贡献 (紫色) | "标出 novelty 和 contributions" |
| `summarize-paper` | 生成结构化笔记 | "给这篇论文写一段中文摘要笔记" |
| `compare-papers` | 对比多篇论文 | "对比这3篇论文的方法差异" |
| `extract-tables` | 提取论文表格数据 | "把 Table 2 提取为 CSV" |
| `review-paper` | 模拟审稿意见 | "以 MICRO 审稿人视角评审" |

### 用户自定义 Prompt 模板

```yaml
name: "我的标注规范"
description: "体系结构论文标注"
rules:
  - match: "实验结果、性能数据、speedup、improvement"
    color: "#28CA42"  # 绿
    label: "Result"
  - match: "提出的方法、算法、架构设计"
    color: "#2EA8E5"  # 蓝
    label: "Method"
  - match: "limitation、future work、不足"
    color: "#ff6666"  # 红
    label: "Gap"
```

---

## API 接口规范

### 标注接口

```http
POST /api/v1/annotate
Authorization: Bearer <api_key>
Content-Type: application/json

{
  "pdf_url": "https://..." | "zotero://item_key",
  "task": "annotate-findings",
  "pages": [0, 1],           // 可选，默认全文
  "color": "#28CA42",         // 可选，覆盖默认
  "prompt": "...",            // 可选，自定义 prompt
  "output": "zotero" | "json" // 输出到 Zotero 或返回 JSON
}
```

### 响应格式

```json
{
  "annotations": [
    {
      "page": 0,
      "text": "the DCC demonstrates significant improvements...",
      "rect": [36.018, 525.261, 292.435, 537.717],
      "label": "Result",
      "confidence": 0.95
    }
  ],
  "usage": {"tokens": 1200, "cost": 0.02}
}
```

---

## 获客渠道

| 渠道 | 策略 | 目标用户 |
|------|------|----------|
| Zotero 插件市场 | 免费版引流，Pro 版付费 | 学术研究者 |
| 学术社区 | 小红书/知乎/Twitter | 研究生、博后 |
| 高校实验室 | Team 版 B2B | 科研团队 |
| 开发者社区 | API + MCP 生态 | AI 工具开发者 |
| Claude/Cursor 生态 | MCP Server 分发 | 技术型用户 |

---

## 开发路线图

### Phase 1 — MVP（已完成 ✅）
- [x] PDF 文本提取 + 坐标转换
- [x] Zotero 数据库读写
- [x] 高亮标注创建
- [x] 读操作免关闭 Zotero（复制快照读）
- [x] item_id 统一解析（路径/key/数字）
- [x] 搜索功能

### Phase 2 — Zotero 插件桥接
- [ ] 开发 Zotero 7 插件，暴露 HTTP API
- [ ] 写操作通过插件 API 执行（免关闭 Zotero）
- [ ] 实时刷新标注（无需 Ctrl+Shift+R）
- [ ] Collection 级批量操作

### Phase 3 — 云端服务
- [ ] FastAPI 后端 + PostgreSQL
- [ ] 用户认证 + API Key 管理
- [ ] 计费系统（按次/按月）
- [ ] Prompt 模板市场
- [ ] Zotero Web API 对接

### Phase 4 — 生态建设
- [ ] Zotero 插件市场上架
- [ ] Claude Code Skill 发布
- [ ] Cursor MCP Server 适配
- [ ] 团队协作功能
