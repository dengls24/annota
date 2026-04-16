# Zotero MCP 开发踩坑记录

实际开发中遇到的问题和解决方案，供后续开发参考。

---

## 坑 1：Zotero 排他锁 — WAL 模式无效

**预期**：SQLite WAL 模式允许并发读写，设置 `PRAGMA journal_mode=WAL` 就能在 Zotero 运行时读写数据库。

**实际**：Zotero 7 使用 `EXCLUSIVE` 锁模式，WAL 模式完全无效。甚至连 `PRAGMA busy_timeout` 都无法设置（读连接时 PRAGMA 本身就被锁住了）。

**尝试过的方案（按顺序）**：
1. ❌ `PRAGMA journal_mode=WAL` — Zotero 排他锁下无效
2. ❌ `?mode=ro` URI 参数 — 仍然需要共享锁，被排他锁阻止
3. ❌ 去掉 `?mode=ro` 用普通连接 — SELECT 查询仍被锁
4. ✅ **复制数据库文件到临时目录再读取** — 最终方案

**最终方案**：`shutil.copy2()` 复制 `.sqlite` 到 `%TEMP%/zotero_mcp/`，耗时 <100ms（数据库通常 <100MB）。

**教训**：不要假设 WAL 能解决所有并发问题。当目标应用使用排他锁时，复制文件是最简单可靠的方案。

---

## 坑 2：`create_pdf_annotation` vs `get_pdf_layout_text` 接口不对称

**问题**：`get_pdf_layout_text` 的 `item_id` 支持数字 ID 和文件路径，但 `create_pdf_annotation` 只接受数字 ID。用文件路径调用后者会报 `invalid literal for int()`。

**根因**：`create_pdf_annotation` 里直接 `int(item_id)` 没有做路径判断。

**修复**：新增 `_resolve_item_id()` 函数，从文件路径中用正则提取 storage key（`/storage/{8字符KEY}/`），再查数据库得到数字 itemID。

---

## 坑 3：`list_zotero_items` 默认 50 条找不到目标

**问题**：Zotero 库有几百上千条目，默认只返回 50 条，目标论文大概率不在其中。

**修复**：新增 `search_zotero_items` 工具，支持按标题/作者/key 搜索，不再需要遍历全库。

---

## 坑 4：中文路径编码问题

**问题**：`sqlite3 'E:/asic-soc/0-文献&翻译/...'` 在 bash 中报 `unable to open database file`。

**原因**：Windows bash 环境下，中文路径传给 sqlite3 命令时编码不对。

**解决**：
- 命令行方式：先 `cd` 到目录再用相对路径
- Python 方式：用 `f"file:{path}"` URI 格式连接（Python sqlite3 模块处理编码正确）

---

## 坑 5：读连接设置 WAL PRAGMA 会触发写操作

**问题**：`PRAGMA journal_mode=WAL` 本身是一个写操作（修改数据库 journal 模式），在 Zotero 排他锁下会失败。

**教训**：只在写连接上设置 WAL。读连接不需要——WAL 是库级别属性，一旦设置就对所有连接生效。

---

## 坑 6：Zotero 数据目录不在默认位置

**问题**：代码默认去 `C:/Users/{user}/Zotero/` 找数据库，但用户配置了自定义目录 `E:/asic-soc/0-文献&翻译/Zotero文献/`。

**解决**：
- 通过环境变量 `ZOTERO_DATA_DIR` 配置
- 可以从用户给的 PDF 路径反推：`storage` 的父目录就是数据目录

---

## 关键设计决策

| 决策 | 选择 | 原因 |
|------|------|------|
| 读操作锁冲突 | 复制数据库文件 | 最简单、最可靠、无依赖 |
| 写操作锁冲突 | 重试机制 + 提示用户关闭 | 无法绕过排他锁，远期需插件桥接 |
| item_id 解析 | 正则提取 storage key | 兼容路径/key/数字三种格式 |
| 搜索实现 | SQL LIKE 模糊匹配 | 简单有效，无需额外索引 |
