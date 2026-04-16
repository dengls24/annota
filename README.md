# Zotero MCP + AI Skills

An MCP Server + Claude Code Skills for AI-powered academic paper annotation, note-taking, and review via Zotero.

## What is this?

This project turns your Zotero library into an AI-powered research assistant. It provides:

1. **MCP Server** (`zotero_mcp/`) — 9 tools for reading/writing Zotero's PDF annotations and notes
2. **Claude Code Skills** (`.claude/skills/`) — 3 ready-to-use slash commands for common research workflows

## Features

### MCP Server (9 tools)

| Tool | Function | Zotero Running? |
|------|----------|:---:|
| `search_zotero_items` | Search by title/author/key | Yes |
| `list_zotero_items` | List recent items | Yes |
| `get_item_metadata` | Get authors, year, DOI, etc. | Yes |
| `get_pdf_text_bulk` | Bulk text extraction (no coords) | Yes |
| `get_pdf_layout_text` | Single page text + coordinates | Yes |
| `list_annotations` | List existing annotations | Yes |
| `create_pdf_annotation` | Create a highlight/underline | Close Zotero |
| `batch_annotate` | Create multiple annotations at once | Close Zotero |
| `add_child_note` | Add a child note to an item | Close Zotero |

**Key design decisions:**
- Read operations work while Zotero is running (database snapshot copy)
- Write operations require closing Zotero (SQLite exclusive lock)
- Two-phase workflow for large PDFs: bulk text first, then targeted coordinates
- Auto-detect and skip References pages

### Claude Code Skills (3 slash commands)

| Skill | Usage | Function |
|-------|-------|----------|
| `/zotero-annotate` | `/zotero-annotate path/to/paper.pdf highlight findings in green` | Smart annotation with semantic color coding |
| `/zotero-summarize` | `/zotero-summarize path/to/paper.pdf` | Generate structured reading notes |
| `/zotero-review` | `/zotero-review path/to/paper.pdf MICRO` | Simulate peer review |

## Quick Start

### Prerequisites

- Python 3.10+
- [Zotero 7](https://www.zotero.org/download/)
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code)

### Installation

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/zotero-mcp.git
cd zotero-mcp

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate   # Windows

# Install dependencies
pip install pymupdf mcp
```

### Configure Claude Code

Add to your `~/.claude.json` under `mcpServers`:

```json
{
  "mcpServers": {
    "zotero-mcp": {
      "command": "/path/to/zotero-mcp/.venv/bin/python",
      "args": ["/path/to/zotero-mcp/zotero_mcp/server.py"],
      "env": {
        "ZOTERO_DATA_DIR": "/path/to/your/Zotero/data"
      }
    }
  }
}
```

**Finding your Zotero data directory:**
- Zotero > Edit > Settings > Advanced > Files and Folders > Data Directory Location

### Use Skills

Copy `.claude/skills/` to your project or home directory:

```bash
# Project-level (this project only)
cp -r .claude/skills/ /your/project/.claude/skills/

# Global (all projects)
cp -r .claude/skills/ ~/.claude/skills/
```

Then use in Claude Code:
```
/zotero-annotate "E:\papers\my-paper.pdf" highlight all experimental results in green
/zotero-summarize "E:\papers\my-paper.pdf"
/zotero-review "E:\papers\my-paper.pdf" ISCA
```

## Two-Phase Workflow (Large PDFs)

For papers >10 pages, the tools automatically follow a two-phase approach:

```
Phase 1 — Understand (lightweight):
  get_pdf_text_bulk(pdf, skip_refs=True)  → Pure text, no coordinates
  AI reads and identifies target sentences

Phase 2 — Annotate (precise):
  get_pdf_layout_text(pdf, target_page)   → Coordinates for 1-2 pages only
  batch_annotate(pdf, annotations)        → Write all highlights at once
```

**Result:** 20-page paper uses ~3 tool calls instead of ~20, context reduced by 63-80%.

## Color Convention

| Color | Hex | Semantic |
|-------|-----|----------|
| Yellow | `#ffd400` | Default highlight |
| Green | `#28CA42` | Results / Findings |
| Blue | `#2EA8E5` | Methods / Definitions |
| Red | `#ff6666` | Limitations / Issues |
| Purple | `#a28ae5` | Contributions / Novelty |

## Project Structure

```
zotero-mcp/
├── zotero_mcp/              # MCP Server
│   ├── server.py            # Tool registration (9 tools)
│   ├── zotero_db.py         # SQLite read/write layer
│   ├── pdf_tools.py         # PyMuPDF text extraction
│   └── config.py            # Constants
├── .claude/skills/          # Claude Code Skills
│   ├── zotero-annotate/     # Smart annotation
│   ├── zotero-summarize/    # Paper notes
│   └── zotero-review/       # Peer review simulation
├── docs/                    # Documentation
│   ├── zotero-mcp-guide.md  # Usage guide
│   ├── commercial-plan.md   # Commercialization plan
│   ├── large-pdf-design.md  # Large PDF handling design
│   └── dev-notes.md         # Development notes & pitfalls
└── README.md
```

## Known Limitations

- **Write operations require closing Zotero** — Zotero 7 uses SQLite exclusive locks. A Zotero plugin bridge (HTTP API) is planned to solve this.
- **References detection is heuristic** — Works for most papers but may miss unconventional formats.
- **Windows paths** — Tested on Windows. Linux/Mac should work but path handling may need adjustment.

## Roadmap

- [ ] Zotero 7 plugin bridge (write without closing Zotero)
- [ ] Zotero Web API support (cloud library)
- [ ] More skills: `compare-papers`, `extract-tables`
- [ ] Prompt template marketplace
- [ ] Team collaboration features

## License

MIT
