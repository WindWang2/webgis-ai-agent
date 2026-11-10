"""G17 导入工作台 —— 有界资源预算（任务书 §定向验证）。

所有上限是产品合同：超限必须有界拒绝并清理，而不是无界消耗。
数值集中于此，便于审查与测试钉扎。
"""
from __future__ import annotations

# ── 批次 / 文件 ────────────────────────────────────────────────────────────
MAX_FILES_PER_BATCH = 20
# 单文件沿用既有上传上限（data_parser.MAX_VECTOR_SIZE = 50MB）
MAX_BATCH_RAW_BYTES = 200 * 1024 * 1024        # 批次原始字节合计
MAX_BATCH_EXPANDED_BYTES = 200 * 1024 * 1024   # zip 展开后合计
MAX_MEMBERS_PER_CONTAINER = 64                  # GPKG/KML 图层、XLSX sheet
MAX_ZIP_ENTRIES = 4096
MAX_ZIP_NESTING = 1                             # 嵌套 zip → 不支持（披露）
SUPPORTED_EXTS = {".geojson", ".json", ".gpkg", ".kml", ".zip", ".csv", ".xlsx"}

# ── 预览 / 样本 ────────────────────────────────────────────────────────────
PREVIEW_DEFAULT_ROWS = 200
PREVIEW_MAX_ROWS = 2000
REJECT_INLINE_MAX = 100                          # 内联拒绝行样本上限
RAW_EXCERPT_MAX_CHARS = 200                      # 单拒绝行原文摘录上限
MAX_COLUMNS_SHOWN = 64                           # 成员清单字段上限（超出计数披露）
SAMPLE_COLUMNS_IN_PREVIEW = 64

# ── 提交 ───────────────────────────────────────────────────────────────────
COMMIT_ROW_LIMIT = 500_000                       # 单成员提交行预算（超出诚实失败）
COMMIT_CANCEL_CHECK_ROWS = 1000                  # 逐行循环取消检查间隔
MAX_MEMBER_OUTPUT_BYTES = 150 * 1024 * 1024      # 成员 GeoJSON 输出预算（序列化后）
CONCURRENT_MEMBER_PARSE = 1                      # 默认同时解析 1 个成员

# ── staging 生命周期 ───────────────────────────────────────────────────────
BATCH_TTL_HOURS = 48
MAX_BATCHES_PER_SESSION = 20                     # 超出时最旧未提交批次先过期

# ── XLSX ───────────────────────────────────────────────────────────────────
XLSX_SHEET_SCAN_ROWS = 200                       # 发现期每 sheet 采样行
XLSX_MAX_SHEETS = 64
