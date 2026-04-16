"""PDF 文本提取与坐标转换。

使用 PyMuPDF (fitz) 解析 PDF 页面，提取文本块及其物理坐标，
并将坐标从 PyMuPDF 空间（左上角原点）转换为 Zotero PDF user space（左下角原点）。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)


# ── 坐标转换 ────────────────────────────────────────────────────

def pymupdf_rect_to_zotero(rect: tuple[float, ...], page_height: float) -> list[float]:
    """PyMuPDF rect (左上角原点, y↓) → Zotero rect (左下角原点, y↑)。

    PyMuPDF:  (x0, y0_top, x1, y1_top)  y0 < y1, y 从上往下
    Zotero:   [x0, y0_bot, x1, y1_bot]  y0 < y1, y 从下往上
    转换: zotero_y0 = H - pymupdf_y1,  zotero_y1 = H - pymupdf_y0
    """
    x0, y0, x1, y1 = rect[:4]
    return [
        round(x0, 3),
        round(page_height - y1, 3),
        round(x1, 3),
        round(page_height - y0, 3),
    ]


def zotero_rect_to_pymupdf(rect: list[float], page_height: float) -> tuple[float, ...]:
    """Zotero rect → PyMuPDF rect（逆变换）。"""
    x0, y0, x1, y1 = rect
    return (x0, page_height - y1, x1, page_height - y0)


# ── 文本提取 ────────────────────────────────────────────────────

def extract_page_text(
    pdf_path: str | Path,
    page_number: int,
) -> dict:
    """提取指定 PDF 页面的文本块及其 Zotero 空间坐标。

    Returns:
        {
            "page_number": int,
            "page_width": float,
            "page_height": float,
            "blocks": [
                {"text": str, "rect": [x0, y0, x1, y1]},
                ...
            ]
        }
    """
    doc = fitz.open(str(pdf_path))
    try:
        if page_number < 0 or page_number >= len(doc):
            raise ValueError(
                f"page_number {page_number} 超出范围，"
                f"该 PDF 共 {len(doc)} 页 (0-indexed)"
            )

        page = doc[page_number]
        page_height = page.rect.height
        page_width = page.rect.width

        # 使用 dict 模式获取结构化文本（blocks → lines → spans）
        text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)

        blocks = []
        for block in text_dict["blocks"]:
            if block["type"] != 0:  # 只处理文本块，跳过图片块
                continue

            for line in block["lines"]:
                # 合并同一行所有 span 的文本
                line_text = ""
                for span in line["spans"]:
                    line_text += span["text"]

                line_text = line_text.strip()
                if not line_text:
                    continue

                # 用行的整体 bbox 作为坐标
                line_rect = line["bbox"]  # (x0, y0, x1, y1) PyMuPDF 坐标
                zotero_rect = pymupdf_rect_to_zotero(line_rect, page_height)

                blocks.append({
                    "text": line_text,
                    "rect": zotero_rect,
                })

        logger.info(
            "提取页面 %d: %d 个文本行, 页面尺寸 %.1f x %.1f",
            page_number, len(blocks), page_width, page_height,
        )

        return {
            "page_number": page_number,
            "page_width": round(page_width, 3),
            "page_height": round(page_height, 3),
            "blocks": blocks,
        }
    finally:
        doc.close()


def extract_bulk_text(
    pdf_path: str | Path,
    pages: list[int] | None = None,
    skip_refs: bool = True,
) -> dict:
    """批量提取多页 PDF 纯文本（无坐标），适合内容理解阶段。

    与 extract_page_text 的区别：不返回坐标，体积缩小 ~80%。
    用于两阶段工作流的第一阶段：先理解内容，再对目标页获取精确坐标。

    Args:
        pdf_path: PDF 文件路径
        pages: 要提取的页码列表（0-indexed），None = 全文
        skip_refs: 是否自动跳过参考文献页（默认 True）

    Returns:
        {
            "total_pages": int,
            "extracted_pages": int,
            "refs_start_page": int | None,
            "pages": [
                {"page": int, "text": str, "char_count": int},
                ...
            ]
        }
    """
    doc = fitz.open(str(pdf_path))
    try:
        total_pages = len(doc)

        # 检测参考文献起始页
        refs_start = _detect_refs_page(doc) if skip_refs else total_pages

        # 确定要提取的页码
        if pages is not None:
            target_pages = [p for p in pages if 0 <= p < total_pages]
        else:
            target_pages = list(range(min(refs_start, total_pages)))

        result_pages = []
        for pn in target_pages:
            page = doc[pn]
            text = page.get_text("text").strip()
            if text:
                result_pages.append({
                    "page": pn,
                    "text": text,
                    "char_count": len(text),
                })

        logger.info(
            "批量提取: %d/%d 页, 跳过参考文献=%s (refs_start=%s)",
            len(result_pages), total_pages, skip_refs,
            refs_start if skip_refs else "N/A",
        )

        return {
            "total_pages": total_pages,
            "extracted_pages": len(result_pages),
            "refs_start_page": refs_start if skip_refs and refs_start < total_pages else None,
            "pages": result_pages,
        }
    finally:
        doc.close()


def _detect_refs_page(doc) -> int:
    """启发式检测参考文献起始页。

    从最后 10 页往前扫描，寻找 References / Bibliography / 参考文献 标题。
    返回参考文献起始页码（0-indexed），未找到则返回总页数。
    """
    total = len(doc)
    scan_start = max(0, total - 10)

    for i in range(total - 1, scan_start - 1, -1):
        text = doc[i].get_text("text")
        lines = text.split("\n")
        for line in lines[:15]:  # 只检查页面前 15 行
            stripped = line.strip().lower()
            if stripped in (
                "references",
                "bibliography",
                "参考文献",
                "reference",
            ):
                return i

    return total  # 未检测到


def get_total_pages(pdf_path: str | Path) -> int:
    """返回 PDF 总页数。"""
    doc = fitz.open(str(pdf_path))
    try:
        return len(doc)
    finally:
        doc.close()
