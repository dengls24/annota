"""Zotero MCP Server — 主入口。

通过 FastMCP 注册以下工具：
  - get_pdf_layout_text:   提取 PDF 页面文本及 Zotero 空间坐标
  - get_pdf_text_bulk:     批量提取多页纯文本（无坐标，适合大 PDF）
  - create_pdf_annotation: 在 Zotero 中创建 PDF 高亮批注
  - batch_annotate:        一次性创建多条标注（减少调用次数）
  - add_child_note:        为 Zotero 条目创建子笔记
  - list_zotero_items:     列出 Zotero 库中的文献条目
  - search_zotero_items:   按标题/作者/key 搜索 Zotero 条目
  - get_item_metadata:     获取条目元数据（作者、年份、期刊等）
  - list_annotations:      列出 PDF 上已有的标注

运行方式: python server.py  (stdio transport, 供 Claude Code 调用)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# ── 必须最先执行：将项目根目录加入 sys.path ────────────────────────
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import json
import logging
import sqlite3

from mcp.server.fastmcp import FastMCP

from zotero_mcp import pdf_tools, zotero_db
from zotero_mcp.config import ZOTERO_DB_PATH, ZOTERO_STORAGE_DIR

# ── 日志配置（输出到 stderr，不干扰 stdio JSON-RPC）──────────────
logging.basicConfig(
    level=logging.INFO,
    format="[zotero-mcp] %(levelname)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

# ── MCP Server ──────────────────────────────────────────────────
mcp = FastMCP(
    name="zotero-mcp",
    instructions=(
        "Zotero MCP Server: 读取本地 Zotero 库中的 PDF 文件，提取带坐标的文本，"
        "并精准回写高亮批注和笔记。所有坐标均为 PDF user space（原点左下角）。\n\n"
        "【大 PDF 两阶段工作流】处理超过 10 页的论文时，请遵循：\n"
        "  Phase 1 — 理解：先用 get_pdf_text_bulk 批量提取纯文本（无坐标），让 LLM 理解全文内容，"
        "确定哪些页面的哪些句子需要标注。\n"
        "  Phase 2 — 定位：只对目标页调用 get_pdf_layout_text 获取精确坐标，"
        "然后用 create_pdf_annotation 写入标注。\n"
        "  切勿对每一页都调用 get_pdf_layout_text，这会导致上下文溢出。"
    ),
)


# ── Tool 1: get_pdf_layout_text ─────────────────────────────────

@mcp.tool()
def get_pdf_layout_text(item_id: str, page_number: int) -> str:
    """提取 PDF 指定页面的文本及物理坐标。

    返回 JSON，每个文本行包含 text 和 rect [x0, y0, x1, y1]（Zotero PDF 坐标系）。
    可以直接将 rect 传给 create_pdf_annotation 使用。

    Args:
        item_id: Zotero PDF 附件的 itemID（数字），或 PDF 文件的绝对路径
        page_number: 页码（从 0 开始）
    """
    pdf_path = _resolve_pdf_path(item_id)

    result = pdf_tools.extract_page_text(pdf_path, page_number)
    return json.dumps(result, ensure_ascii=False, indent=2)


# ── Tool 2: create_pdf_annotation ───────────────────────────────

@mcp.tool()
def create_pdf_annotation(
    item_id: str,
    page_index: int,
    rects: list[list[float]],
    color: str = "#ffd400",
    comment: str = "",
    text: str = "",
    type: str = "highlight",
) -> str:
    """在 Zotero PDF 上创建高亮/下划线批注。

    坐标使用 get_pdf_layout_text 返回的 rect 值，无需手动转换。
    写入后需重启 Zotero 或按 Ctrl+Shift+R 刷新才能看到批注。

    注意：写操作需要关闭 Zotero 桌面应用，否则会因数据库锁而失败。

    Args:
        item_id: Zotero PDF 附件的 itemID（数字），或 PDF 文件的绝对路径
        page_index: 页码（从 0 开始）
        rects: 坐标数组，每项为 [x0, y0, x1, y1]（来自 get_pdf_layout_text）
        color: 十六进制颜色，如 "#ffd400"(黄), "#28CA42"(绿), "#2EA8E5"(蓝)
        comment: 附加在批注上的文字评论（可选）
        text: 被高亮的原始文本（可选，用于 Zotero 中显示）
        type: 批注类型: "highlight"(默认) 或 "underline"
    """
    try:
        attachment_id = _resolve_item_id(item_id)

        # 获取 page_height（用于 sortIndex 计算）
        pdf_path = _resolve_pdf_path(item_id)
        page_data = pdf_tools.extract_page_text(pdf_path, page_index)
        page_height = page_data["page_height"]

        result = zotero_db.create_annotation(
            parent_attachment_id=attachment_id,
            page_index=page_index,
            rects=rects,
            page_height=page_height,
            color=color,
            comment=comment,
            text=text,
            ann_type=type,
        )
        return json.dumps({
            **result,
            "message": "批注已写入 Zotero 数据库。请重启 Zotero 或按 Ctrl+Shift+R 刷新查看。",
        }, ensure_ascii=False)
    except sqlite3.OperationalError as e:
        if "locked" in str(e).lower():
            return json.dumps({
                "error": "database_locked",
                "message": "Zotero 数据库被锁定，请先关闭 Zotero 桌面应用再重试。",
            }, ensure_ascii=False)
        raise


# ── Tool 3: add_child_note ──────────────────────────────────────

@mcp.tool()
def add_child_note(parent_item_id: str, note_content: str) -> str:
    """为 Zotero 文献条目创建子笔记。

    支持 HTML 和纯文本。写入后需重启 Zotero 或按 Ctrl+Shift+R 刷新。
    注意：写操作需要关闭 Zotero 桌面应用。

    Args:
        parent_item_id: 父文献条目的 itemID（数字字符串）
        note_content: 笔记内容（HTML 或纯文本，支持 Markdown 风格）
    """
    try:
        result = zotero_db.create_child_note(
            parent_item_id=int(parent_item_id),
            note_content=note_content,
        )
        return json.dumps({
            **result,
            "message": "笔记已写入 Zotero 数据库。请重启 Zotero 或按 Ctrl+Shift+R 刷新查看。",
        }, ensure_ascii=False)
    except sqlite3.OperationalError as e:
        if "locked" in str(e).lower():
            return json.dumps({
                "error": "database_locked",
                "message": "Zotero 数据库被锁定，请先关闭 Zotero 桌面应用再重试。",
            }, ensure_ascii=False)
        raise


# ── Tool 4: list_zotero_items ──────────────────────────────────

@mcp.tool()
def list_zotero_items(limit: int = 50) -> str:
    """列出 Zotero 库中的文献条目。

    返回每个条目的 itemID、key、标题，以及 PDF 附件 ID（如有）。
    用于发现 item_id 以供其他工具使用。

    Args:
        limit: 最多返回条目数（默认 50）
    """
    items = zotero_db.list_items(limit=limit)
    return json.dumps(items, ensure_ascii=False, indent=2)


# ── Tool 5: search_zotero_items ────────────────────────────────

@mcp.tool()
def search_zotero_items(query: str, limit: int = 20) -> str:
    """按标题、作者或 key 搜索 Zotero 条目。

    比 list_zotero_items 更高效，可直接定位目标论文。

    Args:
        query: 搜索关键词（标题/作者的部分文字，或 Zotero item key）
        limit: 最多返回条目数（默认 20）
    """
    items = zotero_db.search_items(query=query, limit=limit)
    return json.dumps(items, ensure_ascii=False, indent=2)


# ── Tool 6: get_pdf_text_bulk ─────────────────────────────────

@mcp.tool()
def get_pdf_text_bulk(
    item_id: str,
    pages: list[int] | None = None,
    skip_refs: bool = True,
) -> str:
    """批量提取多页 PDF 纯文本（无坐标），适合大 PDF 内容理解。

    与 get_pdf_layout_text 的区别：不返回坐标，context 占用减少 ~80%。
    推荐工作流：
      1. 先用此工具理解全文 → 确定目标页和目标句子
      2. 再用 get_pdf_layout_text 获取目标页的精确坐标
      3. 最后用 create_pdf_annotation 写入标注

    Args:
        item_id: Zotero PDF 附件的 itemID（数字），或 PDF 文件的绝对路径
        pages: 要提取的页码列表（0-indexed），不传则提取全文
        skip_refs: 是否自动跳过参考文献页（默认 True）
    """
    pdf_path = _resolve_pdf_path(item_id)
    result = pdf_tools.extract_bulk_text(pdf_path, pages=pages, skip_refs=skip_refs)
    return json.dumps(result, ensure_ascii=False, indent=2)


# ── Tool 7: get_item_metadata ─────────────────────────────────

@mcp.tool()
def get_item_metadata(item_id: str) -> str:
    """获取 Zotero 条目的完整元数据。

    返回标题、作者列表、年份、期刊、DOI 等信息。
    支持传入文献条目 ID 或 PDF 附件 ID（自动查找父条目）。

    Args:
        item_id: Zotero 条目或 PDF 附件的 itemID（数字），或 PDF 文件路径
    """
    numeric_id = _resolve_item_id(item_id)
    result = zotero_db.get_item_metadata(numeric_id)
    return json.dumps(result, ensure_ascii=False, indent=2)


# ── Tool 8: list_annotations ──────────────────────────────────

@mcp.tool()
def list_annotations(item_id: str) -> str:
    """列出 PDF 附件上已有的所有标注。

    用于检查已有标注，避免重复标注。返回每条标注的类型、颜色、文本和评论。

    Args:
        item_id: Zotero PDF 附件的 itemID（数字），或 PDF 文件的绝对路径
    """
    attachment_id = _resolve_item_id(item_id)
    result = zotero_db.list_annotations(attachment_id)
    return json.dumps(result, ensure_ascii=False, indent=2)


# ── Tool 9: batch_annotate ────────────────────────────────────

@mcp.tool()
def batch_annotate(
    item_id: str,
    annotations: list[dict],
) -> str:
    """一次性创建多条 PDF 标注（减少调用次数）。

    每条标注需包含 page_index 和 rects，可选 color/text/comment/type。
    写操作需要关闭 Zotero 桌面应用。

    Args:
        item_id: Zotero PDF 附件的 itemID（数字），或 PDF 文件的绝对路径
        annotations: 标注列表，每项为:
            {"page_index": int, "rects": [[x0,y0,x1,y1],...],
             "color": str, "text": str, "comment": str, "type": str}
    """
    try:
        attachment_id = _resolve_item_id(item_id)
        pdf_path = _resolve_pdf_path(item_id)

        # 缓存每页的 page_height，避免重复解析
        page_heights: dict[int, float] = {}
        results = []

        for ann in annotations:
            page_idx = ann["page_index"]
            rects = ann["rects"]

            if page_idx not in page_heights:
                page_data = pdf_tools.extract_page_text(pdf_path, page_idx)
                page_heights[page_idx] = page_data["page_height"]

            result = zotero_db.create_annotation(
                parent_attachment_id=attachment_id,
                page_index=page_idx,
                rects=rects,
                page_height=page_heights[page_idx],
                color=ann.get("color", "#ffd400"),
                comment=ann.get("comment", ""),
                text=ann.get("text", ""),
                ann_type=ann.get("type", "highlight"),
            )
            results.append(result)

        return json.dumps({
            "created": len(results),
            "annotations": results,
            "message": f"已创建 {len(results)} 条标注。请重启 Zotero 或按 Ctrl+Shift+R 刷新查看。",
        }, ensure_ascii=False)
    except sqlite3.OperationalError as e:
        if "locked" in str(e).lower():
            return json.dumps({
                "error": "database_locked",
                "message": "Zotero 数据库被锁定，请先关闭 Zotero 桌面应用再重试。",
            }, ensure_ascii=False)
        raise


# ── 内部辅助 ────────────────────────────────────────────────────

# 匹配 Zotero storage 路径中的 key: .../storage/{8字符KEY}/...
_STORAGE_KEY_RE = re.compile(r"[/\\]storage[/\\]([A-Z0-9]{8})[/\\]", re.IGNORECASE)


def _resolve_pdf_path(item_id: str) -> Path:
    """将 item_id 解析为 PDF 文件路径。

    如果是纯数字，按 Zotero itemID 查找；
    如果包含路径分隔符，按文件路径处理。
    """
    if "/" in item_id or "\\" in item_id or ":" in item_id:
        p = Path(item_id)
        if not p.exists():
            raise FileNotFoundError(f"PDF 文件不存在: {item_id}")
        return p

    attachment_id = int(item_id)
    pdf_path = zotero_db.get_pdf_path(attachment_id)
    if pdf_path is None:
        raise FileNotFoundError(
            f"在 Zotero 数据库中未找到 itemID={attachment_id} 对应的 PDF 文件"
        )
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF 文件在磁盘上不存在: {pdf_path}")
    return pdf_path


def _resolve_item_id(item_id: str) -> int:
    """将 item_id（数字或文件路径）统一解析为 Zotero 数字 itemID。

    支持三种输入：
    1. 纯数字字符串 "6667" → 直接返回 6667
    2. 文件路径 ".../storage/4SQST7E8/xxx.pdf" → 提取 key → 查数据库
    3. Zotero key "4SQST7E8" → 查数据库
    """
    # 纯数字：直接返回
    if item_id.isdigit():
        return int(item_id)

    # 文件路径：从中提取 storage key
    if "/" in item_id or "\\" in item_id or ":" in item_id:
        m = _STORAGE_KEY_RE.search(item_id)
        if m:
            key = m.group(1)
            aid = zotero_db.get_attachment_id_by_key(key)
            if aid is not None:
                return aid
        raise ValueError(
            f"无法从路径中解析 Zotero itemID: {item_id}\n"
            f"请确保路径包含 .../storage/{{KEY}}/... 格式"
        )

    # 可能是 Zotero key
    aid = zotero_db.get_attachment_id_by_key(item_id)
    if aid is not None:
        return aid

    raise ValueError(f"无法解析 item_id: {item_id}")


# ── 入口 ────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("Zotero MCP Server 启动中...")
    logger.info("Zotero 数据库: %s", ZOTERO_DB_PATH)
    logger.info("Zotero 存储目录: %s", ZOTERO_STORAGE_DIR)
    mcp.run(transport="stdio")
