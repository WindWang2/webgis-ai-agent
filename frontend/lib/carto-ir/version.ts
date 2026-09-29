/**
 * CartoIR 版本协商（C11）—— MapSpec schemaVersion 的前端单一协商点。
 *
 * 权威版本词表在 app/lib/cartography/mapspec_schema.py（KNOWN_VERSIONS），
 * 经 ts_projection 投影类型、经 tests/cartography/carto_ir_corpus/
 * contract_manifest.json 双向漂移闸锁定本常量。两侧任一侧漂移即红。
 *
 * 语义（与后端 parse_mapspec 同构）：
 *  - 已知版本 → 逐级 upgrader 迁移到 LATEST（1.x 全部纯 additive，
 *    identity 语义升级；升级不改写 version 字段 —— desired-state 事实源
 *    仍是后端 lifecycle/store）；
 *  - 未知更高版本（forward version）→ **fail-safe**：拒绝渲染该 spec
 *    （渲染器可能不认识新键/新语义 —— 静默渲染就是契约谎言），保留
 *    last-good spec 并产出结构化披露；
 *  - 缺失/非字符串 → 视为 "1.0"（与后端 DEFAULT_VERSION 同口径）并披露。
 *
 * Component ABI（layout.composition.component_abi_version）协商同在此：
 * 权威 = app/lib/cartography/component_abi.py COMPONENT_ABI_VERSION。
 * 前端支持的 ABI 版本低于 spec 声明时 → 组件交互面降级（渲染不受影响），
 * 结构化披露；高于 → 正常（向后兼容面）。
 */

/** 前端支持的 MapSpec 契约版本（与后端 KNOWN_VERSIONS 漂移闸锁定）。 */
export const SUPPORTED_MAPSPEC_VERSIONS = [
  "1.0",
  "1.1",
  "1.2",
  "1.3",
  "1.4",
  "1.5",
] as const;

export type SupportedMapSpecVersion = (typeof SUPPORTED_MAPSPEC_VERSIONS)[number];

export const LATEST_SUPPORTED_MAPSPEC_VERSION: SupportedMapSpecVersion = "1.5";

/** 版本缺省口径（= 后端 DEFAULT_VERSION）。 */
export const DEFAULT_MAPSPEC_VERSION = "1.0";

/** 前端支持的 Component ABI 形状版本（= 后端 COMPONENT_ABI_VERSION）。 */
export const SUPPORTED_COMPONENT_ABI_VERSION = 1;

export type MigrationDisclosure = {
  path: string;
  kind: "unknown" | "invalid" | "policy";
  detail: string;
};

export type MigratedMapSpec =
  | {
      ok: true;
      /** 迁移后的 spec（输入的深拷贝；version 字段保持原值 —— 迁移是
       * 语义升级而非存储改写）。 */
      spec: Record<string, unknown>;
      originalVersion: SupportedMapSpecVersion;
      effectiveVersion: SupportedMapSpecVersion;
      migrated: boolean;
      disclosures: MigrationDisclosure[];
    }
  | {
      ok: false;
      /** forward_version = spec 声明了比本前端更新的契约版本；
       *  invalid_shape = spec 根本不是对象（渲染器无从协商）。 */
      reason: "forward_version" | "invalid_shape";
      version: string;
      disclosures: MigrationDisclosure[];
    };

function isKnownVersion(v: string): v is SupportedMapSpecVersion {
  return (SUPPORTED_MAPSPEC_VERSIONS as readonly string[]).includes(v);
}

export type MapSpecVersionCheck =
  | { ok: true; /** spec 上声明的原始 version（undefined = 缺失/非字符串）。 */
      declaredVersion: string | undefined }
  | { ok: false; reason: "forward_version" | "invalid_shape"; version: string };

/**
 * 版本协商的**免克隆判定**（渲染门共用 —— live reconcile 与 headless
 * compile 同一裁决语义，见 S3 review P2-1/P2-2）：
 *  - 非 object → invalid_shape；
 *  - version 缺失/非字符串 → 视为缺省 1.0（后端 `_version_of` 同口径，
 *    存量 spec 行为不变），ok 放行；
 *  - 已知词表版本 → ok 放行（零拷贝 —— 调用方继续用原 spec 对象）；
 *  - 未知字符串版本（无论更新还是更旧/垃圾值）→ forward_version
 *    fail-safe（fail-safe 结果一致；披露措辞保持中性 —— 不虚构"更新"）。
 * 需要迁移产物/disclosures 时才走 `migrateMapSpec`（全量深拷贝）。
 */
export function checkMapSpecVersion(input: unknown): MapSpecVersionCheck {
  if (input === null || typeof input !== "object" || Array.isArray(input)) {
    return { ok: false, reason: "invalid_shape", version: "" };
  }
  const raw = (input as Record<string, unknown>).version;
  if (raw === undefined || raw === null || typeof raw !== "string") {
    return { ok: true, declaredVersion: undefined };
  }
  if (isKnownVersion(raw)) return { ok: true, declaredVersion: raw };
  return {
    ok: false,
    reason: "forward_version",
    version: raw,
  };
}

/**
 * MapSpec 版本协商与迁移（渲染入口的唯一收口）。
 *
 * 纯函数、无 IO、不修改输入。upgrader 注册表按版本对显式声明 —— 与后端
 * `_UPGRADERS` 同构；1.x 全部 identity（纯 additive），未来破坏性版本
 * 必须在此注册真实 upgrader + 迁移测试（禁止隐式）。
 */
export function migrateMapSpec(input: unknown): MigratedMapSpec {
  if (input === null || typeof input !== "object" || Array.isArray(input)) {
    return {
      ok: false,
      reason: "invalid_shape",
      version: "",
      disclosures: [
        { path: "", kind: "invalid", detail: `spec root is ${input === null ? "null" : Array.isArray(input) ? "array" : typeof input}, expected object` },
      ],
    };
  }

  const doc = input as Record<string, unknown>;
  const rawVersion = doc.version;
  const disclosures: MigrationDisclosure[] = [];

  let version: SupportedMapSpecVersion;
  if (typeof rawVersion !== "string" || rawVersion === "") {
    version = DEFAULT_MAPSPEC_VERSION;
    disclosures.push({
      path: "version",
      kind: "invalid",
      detail: `missing or non-string version (${typeof rawVersion}); assuming ${DEFAULT_MAPSPEC_VERSION}`,
    });
  } else if (!isKnownVersion(rawVersion)) {
    return {
      ok: false,
      reason: "forward_version",
      version: rawVersion,
      disclosures: [
        {
          path: "version",
          kind: "policy",
          // 措辞中性（S3 review P2-3）：词表外版本不一定是"更新"——
          // 更旧/垃圾值同样 fail-safe，不虚构方向诊断。
          detail: `spec version "${rawVersion}" is not in the supported vocabulary (${SUPPORTED_MAPSPEC_VERSIONS.join("/")}); refusing to render (fail-safe)`,
        },
      ],
    };
  } else {
    version = rawVersion;
  }

  const spec: Record<string, unknown> = structuredClone(doc);
  let effective = version;
  let migrated = false;
  const startIdx = SUPPORTED_MAPSPEC_VERSIONS.indexOf(version);
  for (let i = startIdx; i < SUPPORTED_MAPSPEC_VERSIONS.length - 1; i++) {
    const fr = SUPPORTED_MAPSPEC_VERSIONS[i];
    const to = SUPPORTED_MAPSPEC_VERSIONS[i + 1];
    const upgrader = UPGRADERS[`${fr}->${to}`];
    if (!upgrader) break; // 未注册的升级路径：停在当前版本（诚实不虚构）
    upgrader(spec, disclosures);
    effective = to;
    migrated = true;
  }

  return {
    ok: true,
    spec,
    originalVersion: version,
    effectiveVersion: effective,
    migrated,
    disclosures,
  };
}

type Upgrader = (spec: Record<string, unknown>, disclosures: MigrationDisclosure[]) => void;

/** 逐级 upgrader 注册表（与后端 _UPGRADERS 同构；键 "from->to"）。 */
const UPGRADERS: Record<string, Upgrader> = {
  // 1.1–1.5 全部纯 additive：可选键缺失 = 既有语义，identity 升级即可。
  "1.0->1.1": identityUpgrade,
  "1.1->1.2": identityUpgrade,
  "1.2->1.3": identityUpgrade,
  "1.3->1.4": identityUpgrade,
  "1.4->1.5": identityUpgrade,
};

function identityUpgrade(_spec: Record<string, unknown>, _d: MigrationDisclosure[]): void {
  /* 纯 additive：无键改写。 */
}

// ── Component ABI 协商（layout.composition.component_abi_version）────────

export type ComponentAbiCheck =
  | { status: "ok"; abiVersion: number }
  | { status: "abi_newer"; abiVersion: number; detail: string }
  | { status: "abi_missing"; abiVersion: null; detail: string };

/**
 * Component ABI 版本协商。spec 的 layout.composition 携带后端
 * component_abi 写入的身份块；前端支持的形状版本低于声明时，组件
 * *交互*面降级（图层渲染不受影响 —— composition 是注释性语义层）。
 */
export function checkComponentAbi(
  composition: unknown,
): ComponentAbiCheck {
  if (composition === null || typeof composition !== "object") {
    return {
      status: "abi_missing",
      abiVersion: null,
      detail: "layout.composition missing or not an object; no component ABI to negotiate",
    };
  }
  const raw = (composition as Record<string, unknown>).component_abi_version;
  const v = typeof raw === "number" && Number.isFinite(raw) ? raw : NaN;
  if (Number.isNaN(v)) {
    return {
      status: "abi_missing",
      abiVersion: null,
      detail: "layout.composition.component_abi_version missing or non-numeric",
    };
  }
  if (v < 1) {
    // S3 review P3-8：词表下界 —— 0/负数是坏身份，按缺失协商（不虚构
    // "已支持"）。
    return {
      status: "abi_missing",
      abiVersion: null,
      detail: `layout.composition.component_abi_version ${v} is not a valid ABI version (expected >= 1)`,
    };
  }
  if (v > SUPPORTED_COMPONENT_ABI_VERSION) {
    return {
      status: "abi_newer",
      abiVersion: v,
      detail: `composition declares component ABI v${v}; renderer supports v${SUPPORTED_COMPONENT_ABI_VERSION} — component interactions degraded (layers unaffected)`,
    };
  }
  return { status: "ok", abiVersion: v };
}
