"""Zotero MCP Server 配置常量"""

import os
from pathlib import Path

# ── Zotero 数据路径 ──────────────────────────────────────────────
ZOTERO_DATA_DIR = Path(os.environ.get(
    "ZOTERO_DATA_DIR",
    r"E:\asic-soc\0-文献&翻译\Zotero文献"
))
ZOTERO_DB_PATH = ZOTERO_DATA_DIR / "zotero.sqlite"
ZOTERO_STORAGE_DIR = ZOTERO_DATA_DIR / "storage"

# ── Zotero 数据库常量 ────────────────────────────────────────────
LIBRARY_ID = 1  # 用户本地库

# itemTypeID 映射
ITEM_TYPE_ANNOTATION = 1
ITEM_TYPE_ATTACHMENT = 3
ITEM_TYPE_NOTE = 28

# 批注类型 (itemAnnotations.type)
ANN_HIGHLIGHT = 1
ANN_NOTE = 2
ANN_IMAGE = 3
ANN_INK = 4
ANN_UNDERLINE = 5
ANN_TEXT = 6

ANN_TYPE_MAP = {
    "highlight": ANN_HIGHLIGHT,
    "note": ANN_NOTE,
    "image": ANN_IMAGE,
    "ink": ANN_INK,
    "underline": ANN_UNDERLINE,
    "text": ANN_TEXT,
}

# Zotero key 生成字符集 (8字符)
KEY_CHARSET = "23456789ABCDEFGHIJKLMNPQRSTUVWXYZ"
KEY_LENGTH = 8

# 默认批注颜色
DEFAULT_COLOR = "#ffd400"

# ── SQLite 连接与重试配置 ───────────────────────────────────────
DB_TIMEOUT = 30           # SQLite 连接超时（秒）
DB_RETRY_COUNT = 3        # 锁冲突最大重试次数
DB_RETRY_BASE_DELAY = 1   # 指数退避基础延迟（秒）
