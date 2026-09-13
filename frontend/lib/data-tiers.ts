/**
 * Data Tiers —— 三档特征数据量纲的前端镜像（V11 W3.1，ADR-0163）。
 *
 * 权威实现：``app/lib/cartography/data_tiers.py``（常量值冻结，改动走 ADR）。
 * 本模块只镜像常量；parity 由 tests/cartography/test_data_tiers_and_matrix.py
 * 的源码扫描锁定（两侧数值漂移即红）。
 */

/** 档位一：inline 载体上限（免二次请求）。 */
export const TIER_INLINE_FEATURES = 5000;

/** 档位二：扫描/修复诊断与标注的极端档。 */
export const TIER_SCAN_CAP_FEATURES = 20000;

/** 档位三：导出与数据通道封顶。 */
export const TIER_EXPORT_FEATURES = 50000;
