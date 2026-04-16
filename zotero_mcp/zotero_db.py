"""Zotero SQLite 数据库读写层。

负责：查询条目/附件、写入 PDF 批注、创建子笔记。
所有写操作使用短连接 + WAL 模式以减少与 Zotero 客户端的锁冲突。
"""

from __future__ import annotations

import functools
import json
import logging
import random
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import (
    ANN_HIGHLIGHT,
    ANN_TYPE_MAP,
    DB_RETRY_BASE_DELAY,
    DB_RETRY_COUNT,
    DB_TIMEOUT,
    DEFAULT_COLOR,
    ITEM_TYPE_ANNOTATION,
    ITEM_TYPE_NOTE,
    KEY_CHARSET,
    KEY_LENGTH,
    LIBRARY_ID,
    ZOTERO_DB_PATH,
    ZOTERO_STORAGE_DIR,
)

logger = logging.getLogger(__name__)


# ── 辅助函数 ────────────────────────────────────────────────────

def _connect(readonly: bool = False) -> sqlite3.Connection:
    """创建数据库连接。

    Zotero 使用 EXCLUSIVE 锁模式，运行时外部无法直接读写。
    解决方案：
    - 只读操作：复制数据库到临时文件再读取（毫秒级，安全并发）
    - 写操作：直接连接 + 重试机制（等 Zotero 释放锁的间隙写入）
    """
    if readonly:
        return _connect_readonly_copy()

    conn = sqlite3.connect(
        f"file:{ZOTERO_DB_PATH}",
        uri=True,
        timeout=DB_TIMEOUT,
    )
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={DB_TIMEOUT * 1000}")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _connect_readonly_copy() -> sqlite3.Connection:
    """复制数据库文件后连接，避免与 Zotero 的锁冲突。

    Zotero 在运行时持有排他锁，即使 WAL 模式也无法并发读。
    复制 .sqlite 文件（通常 <100MB，耗时 <100ms）到临时目录后读取。
    """
    import shutil
    import tempfile

    tmp_dir = Path(tempfile.gettempdir()) / "zotero_mcp"
    tmp_dir.mkdir(exist_ok=True)
    tmp_db = tmp_dir / "zotero_readonly.sqlite"

    shutil.copy2(ZOTERO_DB_PATH, tmp_db)

    conn = sqlite3.connect(str(tmp_db), timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _retry_on_lock(fn):
    """装饰器：写操作遇到 database-is-locked 时指数退避重试。"""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        last_err = None
        for attempt in range(DB_RETRY_COUNT + 1):
            try:
                return fn(*args, **kwargs)
            except sqlite3.OperationalError as e:
                if "locked" not in str(e).lower():
                    raise
                last_err = e
                if attempt < DB_RETRY_COUNT:
                    delay = DB_RETRY_BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        "数据库锁冲突 (attempt %d/%d)，%ds 后重试: %s",
                        attempt + 1, DB_RETRY_COUNT, delay, e,
                    )
                    time.sleep(delay)
        raise last_err  # type: ignore[misc]
    return wrapper


def generate_key() -> str:
    """生成 Zotero 风格的 8 字符随机 key。"""
    return "".join(random.choices(KEY_CHARSET, k=KEY_LENGTH))


def _now_iso() -> str:
    """返回 Zotero 使用的 ISO 时间戳格式。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _compute_sort_index(
    page_index: int,
    rects: list[list[float]],
    page_height: float,
) -> str:
    """计算 Zotero sortIndex: 'PPPPP|OOOOOO|TTTTT'。

    - PPPPP:  5位页码
    - OOOOOO: 6位字符偏移（简化为 0）
    - TTTTT:  5位距顶部距离 = page_height - rect_y1（取第一个 rect）
    """
    top_dist = 0
    if rects:
        # rects 已经是 Zotero 空间 [x0, y0_bot, x1, y1_bot]
        # 距顶部距离 = page_height - y1
        top_dist = int(page_height - rects[0][3])
        top_dist = max(0, top_dist)

    return f"{page_index:05d}|{0:06d}|{top_dist:05d}"


# ── 查询函数 ────────────────────────────────────────────────────

def get_pdf_path(item_id: int) -> Path | None:
    """通过 Zotero attachment itemID 获取 PDF 文件的绝对路径。

    Zotero 路径格式: path='storage:<filename>'，
    实际文件位于 ZOTERO_STORAGE_DIR / <item_key> / <filename>。
    其中 item_key 来自 items 表的 key 字段。
    """
    conn = _connect(readonly=True)
    try:
        row = conn.execute(
            """SELECT ia.path, i.key AS item_key
               FROM itemAttachments ia
               JOIN items i ON ia.itemID = i.itemID
               WHERE ia.itemID = ?""",
            (item_id,),
        ).fetchone()
        if not row or not row["path"]:
            return None

        path_str: str = row["path"]
        if path_str.startswith("storage:"):
            filename = path_str[len("storage:"):]
            return ZOTERO_STORAGE_DIR / row["item_key"] / filename

        # 绝对路径或其他格式直接返回
        return Path(path_str)
    finally:
        conn.close()


def list_items(limit: int = 50) -> list[dict]:
    """列出 Zotero 库中的条目（含标题和附件信息）。"""
    conn = _connect(readonly=True)
    try:
        rows = conn.execute("""
            SELECT
                i.itemID,
                i.key,
                i.itemTypeID,
                idv_title.value AS title
            FROM items i
            LEFT JOIN itemData id_title
                ON i.itemID = id_title.itemID
                AND id_title.fieldID = (
                    SELECT fieldID FROM fields WHERE fieldName = 'title'
                )
            LEFT JOIN itemDataValues idv_title
                ON id_title.valueID = idv_title.valueID
            WHERE i.itemTypeID NOT IN (1, 3, 28)
                AND i.libraryID = ?
            ORDER BY i.dateModified DESC
            LIMIT ?
        """, (LIBRARY_ID, limit)).fetchall()

        result = []
        for row in rows:
            item = {
                "itemID": row["itemID"],
                "key": row["key"],
                "title": row["title"] or "(无标题)",
            }
            # 查找 PDF 附件
            att = conn.execute("""
                SELECT ia.itemID AS attID, ia.path, ia.contentType
                FROM itemAttachments ia
                JOIN items ai ON ia.itemID = ai.itemID
                WHERE ia.parentItemID = ?
                    AND ia.contentType = 'application/pdf'
                LIMIT 1
            """, (row["itemID"],)).fetchone()

            if att:
                item["pdf_attachment_id"] = att["attID"]
                item["pdf_path"] = att["path"]

            result.append(item)

        return result
    finally:
        conn.close()


def get_attachment_id_by_key(key: str) -> int | None:
    """通过 Zotero storage key 查找 PDF attachment 的 itemID。

    Zotero 路径: storage/{key}/{filename}.pdf
    key 即 items 表的 key 字段。
    """
    conn = _connect(readonly=True)
    try:
        row = conn.execute(
            "SELECT itemID FROM items WHERE key = ?", (key,)
        ).fetchone()
        return row["itemID"] if row else None
    finally:
        conn.close()


def search_items(query: str, limit: int = 20) -> list[dict]:
    """按标题或作者模糊搜索 Zotero 条目。"""
    conn = _connect(readonly=True)
    try:
        pattern = f"%{query}%"
        rows = conn.execute("""
            SELECT DISTINCT
                i.itemID,
                i.key,
                idv_title.value AS title
            FROM items i
            LEFT JOIN itemData id_title
                ON i.itemID = id_title.itemID
                AND id_title.fieldID = (
                    SELECT fieldID FROM fields WHERE fieldName = 'title'
                )
            LEFT JOIN itemDataValues idv_title
                ON id_title.valueID = idv_title.valueID
            LEFT JOIN itemCreators ic ON i.itemID = ic.itemID
            LEFT JOIN creators c ON ic.creatorID = c.creatorID
            WHERE i.itemTypeID NOT IN (1, 3, 28)
                AND i.libraryID = ?
                AND (
                    idv_title.value LIKE ?
                    OR c.lastName LIKE ?
                    OR c.firstName LIKE ?
                    OR i.key = ?
                )
            ORDER BY i.dateModified DESC
            LIMIT ?
        """, (LIBRARY_ID, pattern, pattern, pattern, query, limit)).fetchall()

        result = []
        for row in rows:
            item = {
                "itemID": row["itemID"],
                "key": row["key"],
                "title": row["title"] or "(无标题)",
            }
            att = conn.execute("""
                SELECT ia.itemID AS attID, ia.path
                FROM itemAttachments ia
                WHERE ia.parentItemID = ?
                    AND ia.contentType = 'application/pdf'
                LIMIT 1
            """, (row["itemID"],)).fetchone()
            if att:
                item["pdf_attachment_id"] = att["attID"]
                item["pdf_path"] = att["path"]
            result.append(item)

        return result
    finally:
        conn.close()


def get_item_metadata(item_id: int) -> dict:
    """获取 Zotero 条目的完整元数据（标题、作者、年份、期刊等）。

    Args:
        item_id: 文献条目或 PDF 附件的 itemID

    Returns:
        {"itemID": int, "key": str, "title": str, "authors": [...],
         "year": str, "publicationTitle": str, "DOI": str, ...}
    """
    conn = _connect(readonly=True)
    try:
        # 如果传入的是附件 ID，先找到父条目
        parent_row = conn.execute(
            "SELECT parentItemID FROM itemAttachments WHERE itemID = ?",
            (item_id,),
        ).fetchone()
        if parent_row and parent_row["parentItemID"]:
            item_id = parent_row["parentItemID"]

        # 基本信息
        item_row = conn.execute(
            "SELECT itemID, key, itemTypeID FROM items WHERE itemID = ?",
            (item_id,),
        ).fetchone()
        if not item_row:
            return {"error": f"itemID={item_id} 不存在"}

        result: dict = {
            "itemID": item_row["itemID"],
            "key": item_row["key"],
        }

        # 所有字段值
        field_rows = conn.execute("""
            SELECT f.fieldName, idv.value
            FROM itemData id
            JOIN fields f ON id.fieldID = f.fieldID
            JOIN itemDataValues idv ON id.valueID = idv.valueID
            WHERE id.itemID = ?
        """, (item_id,)).fetchall()

        for fr in field_rows:
            result[fr["fieldName"]] = fr["value"]

        # 作者列表
        creator_rows = conn.execute("""
            SELECT c.firstName, c.lastName, ct.creatorType
            FROM itemCreators ic
            JOIN creators c ON ic.creatorID = c.creatorID
            JOIN creatorTypes ct ON ic.creatorTypeID = ct.creatorTypeID
            WHERE ic.itemID = ?
            ORDER BY ic.orderIndex
        """, (item_id,)).fetchall()

        result["authors"] = [
            {
                "firstName": cr["firstName"] or "",
                "lastName": cr["lastName"] or "",
                "role": cr["creatorType"],
            }
            for cr in creator_rows
        ]

        # PDF 附件
        att = conn.execute("""
            SELECT ia.itemID AS attID, ia.path
            FROM itemAttachments ia
            WHERE ia.parentItemID = ?
                AND ia.contentType = 'application/pdf'
            LIMIT 1
        """, (item_id,)).fetchone()
        if att:
            result["pdf_attachment_id"] = att["attID"]

        return result
    finally:
        conn.close()


def list_annotations(attachment_id: int) -> list[dict]:
    """列出 PDF 附件上的所有已有标注。

    Args:
        attachment_id: PDF 附件的 itemID

    Returns:
        [{"itemID": int, "key": str, "type": str, "color": str,
          "text": str, "comment": str, "pageLabel": str}, ...]
    """
    # 反向映射 type int → name
    type_name_map = {v: k for k, v in ANN_TYPE_MAP.items()}

    conn = _connect(readonly=True)
    try:
        rows = conn.execute("""
            SELECT
                ia.itemID, i.key,
                ia.type, ia.color, ia.text, ia.comment,
                ia.pageLabel, ia.sortIndex, ia.position
            FROM itemAnnotations ia
            JOIN items i ON ia.itemID = i.itemID
            WHERE ia.parentItemID = ?
            ORDER BY ia.sortIndex
        """, (attachment_id,)).fetchall()

        result = []
        for row in rows:
            ann = {
                "itemID": row["itemID"],
                "key": row["key"],
                "type": type_name_map.get(row["type"], str(row["type"])),
                "color": row["color"],
                "text": row["text"] or "",
                "comment": row["comment"] or "",
                "pageLabel": row["pageLabel"],
            }
            result.append(ann)

        return result
    finally:
        conn.close()


# ── 写入函数 ────────────────────────────────────────────────────

@_retry_on_lock
def create_annotation(
    parent_attachment_id: int,
    page_index: int,
    rects: list[list[float]],
    page_height: float,
    color: str = DEFAULT_COLOR,
    comment: str = "",
    text: str = "",
    ann_type: str = "highlight",
) -> dict:
    """在 Zotero 数据库中创建 PDF 批注。

    Args:
        parent_attachment_id: PDF 附件的 itemID
        page_index: 页码（0-indexed）
        rects: Zotero PDF user space 坐标 [[x0,y0,x1,y1], ...]
        page_height: 页面高度（用于计算 sortIndex）
        color: 十六进制颜色
        comment: 批注评论
        text: 被高亮的文本内容
        ann_type: 批注类型 ("highlight", "underline", "ink" 等)

    Returns:
        {"itemID": int, "key": str}
    """
    type_int = ANN_TYPE_MAP.get(ann_type, ANN_HIGHLIGHT)
    key = generate_key()
    now = _now_iso()

    position = json.dumps({
        "pageIndex": page_index,
        "rects": rects,
    })

    sort_index = _compute_sort_index(page_index, rects, page_height)
    page_label = str(page_index + 1)  # 从1开始的页码标签

    conn = _connect()
    try:
        cursor = conn.cursor()

        # 插入 items 记录
        cursor.execute("""
            INSERT INTO items
                (itemTypeID, dateAdded, dateModified, clientDateModified,
                 libraryID, key, version, synced)
            VALUES (?, ?, ?, ?, ?, ?, 0, 0)
        """, (ITEM_TYPE_ANNOTATION, now, now, now, LIBRARY_ID, key))

        item_id = cursor.lastrowid

        # 插入 itemAnnotations 记录
        cursor.execute("""
            INSERT INTO itemAnnotations
                (itemID, parentItemID, type, authorName, text, comment,
                 color, pageLabel, sortIndex, position, isExternal)
            VALUES (?, ?, ?, '', ?, ?, ?, ?, ?, ?, 0)
        """, (
            item_id,
            parent_attachment_id,
            type_int,
            text,
            comment,
            color,
            page_label,
            sort_index,
            position,
        ))

        conn.commit()
        logger.info(
            "创建批注: itemID=%d, key=%s, page=%d, type=%s, color=%s",
            item_id, key, page_index, ann_type, color,
        )
        return {"itemID": item_id, "key": key}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@_retry_on_lock
def create_child_note(
    parent_item_id: int,
    note_content: str,
) -> dict:
    """为 Zotero 条目创建子笔记。

    Args:
        parent_item_id: 父文献条目的 itemID
        note_content: HTML 格式的笔记内容（Zotero 原生使用 HTML）

    Returns:
        {"itemID": int, "key": str}
    """
    key = generate_key()
    now = _now_iso()

    # 如果不是 HTML，包裹为简单 HTML
    if not note_content.strip().startswith("<"):
        note_content = f"<p>{note_content}</p>"

    # 提取前50字符作为标题
    import re
    title = re.sub(r"<[^>]+>", "", note_content)[:50].strip()

    conn = _connect()
    try:
        cursor = conn.cursor()

        # 插入 items 记录
        cursor.execute("""
            INSERT INTO items
                (itemTypeID, dateAdded, dateModified, clientDateModified,
                 libraryID, key, version, synced)
            VALUES (?, ?, ?, ?, ?, ?, 0, 0)
        """, (ITEM_TYPE_NOTE, now, now, now, LIBRARY_ID, key))

        item_id = cursor.lastrowid

        # 插入 itemNotes 记录
        cursor.execute("""
            INSERT INTO itemNotes (itemID, parentItemID, note, title)
            VALUES (?, ?, ?, ?)
        """, (item_id, parent_item_id, note_content, title))

        conn.commit()
        logger.info(
            "创建笔记: itemID=%d, key=%s, parentID=%d, title=%s",
            item_id, key, parent_item_id, title,
        )
        return {"itemID": item_id, "key": key}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
