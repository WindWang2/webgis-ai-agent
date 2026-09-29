/**
 * CartoIR 契约词表的**前端镜像常量**（C11）。
 *
 * 权威在 app/lib/cartography/mapspec_schema.py；本文件是渲染端消费的
 * 镜像。两侧由 tests/cartography/carto_ir_corpus/contract_manifest.json
 * **双向漂移闸**锁定：backend pytest 断言 JSON ≡ schema 常量，frontend
 * vitest 断言 JSON ≡ 本常量 —— 任意一侧改词表不更新 JSON 即红。
 */

/** MapSpec 已知契约版本（= 后端 KNOWN_VERSIONS）。 */
export const CONTRACT_MAPSPEC_VERSIONS = ["1.0", "1.1", "1.2", "1.3", "1.4", "1.5"] as const;

/** 最新契约版本（= 后端 LATEST_VERSION = 词表末位）。 */
export const CONTRACT_LATEST_MAPSPEC_VERSION: (typeof CONTRACT_MAPSPEC_VERSIONS)[number] =
  CONTRACT_MAPSPEC_VERSIONS[CONTRACT_MAPSPEC_VERSIONS.length - 1];

/** 可见性提示词表（= 后端 VISIBILITY_HINT_KEYS）。 */
export const CONTRACT_VISIBILITY_HINT_KEYS = [
  "street_detail_minzoom",
  "admin_boundary_detail",
] as const;

/** bivariate 色阵合法规模（= 后端 BIVARIATE_MATRIX_SIZES）。 */
export const CONTRACT_BIVARIATE_MATRIX_SIZES = [2, 3] as const;

/** bivariate 类别索引属性缺省（= 后端 BIVARIATE_CLASS_FIELD_DEFAULT）。 */
export const CONTRACT_BIVARIATE_CLASS_FIELD_DEFAULT = "__biv_class";

/** data_binding 字段型词表（= 后端 DATA_BINDING_FIELD_TYPES）。 */
export const CONTRACT_DATA_BINDING_FIELD_TYPES = [
  "number",
  "string",
  "boolean",
  "date",
] as const;

/** Component ABI 形状版本（= 后端 COMPONENT_ABI_VERSION）。 */
export const CONTRACT_COMPONENT_ABI_VERSION = 1;
