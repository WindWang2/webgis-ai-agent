import { describe, it, expect } from "vitest";
import {
  SUPPORTED_MAPSPEC_VERSIONS,
  LATEST_SUPPORTED_MAPSPEC_VERSION,
  DEFAULT_MAPSPEC_VERSION,
  migrateMapSpec,
  checkComponentAbi,
  type MigratedMapSpec,
} from "./version";

/**
 * C11 版本协商测试 —— MapSpec schemaVersion 的前端单一协商点。
 *
 * 锁定四个契约面：
 *  1. 已知版本 → 逐级 identity upgrader 迁移到 LATEST（1.5）；
 *  2. forward version（未知更高版本）→ fail-safe 拒绝（ok:false +
 *     policy 披露）—— 静默渲染未知版本就是契约谎言；
 *  3. 缺失/非字符串 version → 视为 "1.0"（与后端 DEFAULT_VERSION 同口径）
 *     并产出 invalid 披露（注意：非字符串**不走** forward 拒绝）；
 *  4. 迁移是语义升级而非存储改写：version 字段保持原值、输入不被突变。
 */

/** 收窄 union：断言 ok 分支并返回（避免测试里出现裸 cast 链）。 */
function expectMigratedOk(result: MigratedMapSpec): Extract<MigratedMapSpec, { ok: true }> {
  expect(result.ok).toBe(true);
  if (!result.ok) throw new Error(`expected ok:true, got ${JSON.stringify(result)}`);
  return result;
}

/** 收窄 union：断言失败分支并返回。 */
function expectMigratedFail(result: MigratedMapSpec): Extract<MigratedMapSpec, { ok: false }> {
  expect(result.ok).toBe(false);
  if (result.ok) throw new Error("expected ok:false");
  return result;
}

/** 深冻结（防突变断言用）。 */
function deepFreeze<T>(value: T): T {
  if (value !== null && typeof value === "object") {
    for (const key of Object.getOwnPropertyNames(value)) {
      deepFreeze((value as Record<string, unknown>)[key]);
    }
    Object.freeze(value);
  }
  return value;
}

describe("migrateMapSpec：已知版本词表", () => {
  it("SUPPORTED_MAPSPEC_VERSIONS 词表与契约镜像锁定到 1.5", () => {
    expect(SUPPORTED_MAPSPEC_VERSIONS).toEqual(["1.0", "1.1", "1.2", "1.3", "1.4", "1.5"]);
    expect(LATEST_SUPPORTED_MAPSPEC_VERSION).toBe("1.5");
    expect(DEFAULT_MAPSPEC_VERSION).toBe("1.0");
  });

  it("已知版本逐个协商 → ok，originalVersion 保真，effective 迁到 1.5", () => {
    for (const v of SUPPORTED_MAPSPEC_VERSIONS) {
      const result = expectMigratedOk(migrateMapSpec({ version: v, layers: [] }));
      expect(result.originalVersion).toBe(v);
      expect(result.effectiveVersion).toBe("1.5");
      expect(result.spec.layers).toEqual([]);
    }
  });

  it("同版本（已是最新的 1.5）→ migrated=false 且无披露", () => {
    const result = expectMigratedOk(migrateMapSpec({ version: "1.5", layers: [] }));
    expect(result.migrated).toBe(false);
    expect(result.effectiveVersion).toBe("1.5");
    expect(result.disclosures).toEqual([]);
  });

  it("旧已知版本 → migrated=true（逐级 identity 升级）且无披露", () => {
    for (const v of ["1.0", "1.1", "1.2", "1.3", "1.4"] as const) {
      const result = expectMigratedOk(migrateMapSpec({ version: v, layers: [] }));
      expect(result.migrated).toBe(true);
      expect(result.disclosures).toEqual([]);
    }
  });

  it('"1.2" 多步 identity 迁移 → effective "1.5"、migrated=true', () => {
    const result = expectMigratedOk(migrateMapSpec({ version: "1.2", layers: [{ id: "a" }] }));
    expect(result.effectiveVersion).toBe("1.5");
    expect(result.migrated).toBe(true);
    // identity 升级不改语义内容
    expect(result.spec.layers).toEqual([{ id: "a" }]);
  });

  it("迁移不改写 version 字段（desired-state 事实源仍在后端）", () => {
    const result = expectMigratedOk(migrateMapSpec({ version: "1.2", layers: [] }));
    expect(result.spec.version).toBe("1.2");
  });

  it("返回深拷贝：result.spec 与输入不同引用", () => {
    const input = { version: "1.2", layers: [{ id: "a" }] };
    const result = expectMigratedOk(migrateMapSpec(input));
    expect(result.spec).not.toBe(input);
    expect(result.spec.layers).not.toBe(input.layers);
  });
});

describe("migrateMapSpec：缺失/非字符串 version → 视为 1.0 并披露", () => {
  it("version 缺失 → ok、originalVersion '1.0'、invalid 披露", () => {
    const result = expectMigratedOk(migrateMapSpec({ layers: [] }));
    expect(result.originalVersion).toBe(DEFAULT_MAPSPEC_VERSION);
    expect(result.effectiveVersion).toBe("1.5");
    expect(result.disclosures).toHaveLength(1);
    expect(result.disclosures[0]?.path).toBe("version");
    expect(result.disclosures[0]?.kind).toBe("invalid");
    expect(result.disclosures[0]?.detail).toContain("assuming 1.0");
  });

  it("version 为 null → 同样走缺省 1.0（不拒绝）", () => {
    const result = expectMigratedOk(migrateMapSpec({ version: null, layers: [] }));
    expect(result.originalVersion).toBe("1.0");
    expect(result.disclosures[0]?.kind).toBe("invalid");
    expect(result.disclosures[0]?.detail).toContain("non-string version (object)");
  });

  it("version 为数字 3 → 非字符串按缺失口径走 1.0（不是 forward_version）", () => {
    // 实现语义：typeof !== "string" 一律视为缺失 → DEFAULT "1.0" + invalid
    // 披露；forward fail-safe 只针对「是字符串但不在词表」的版本。
    const result = expectMigratedOk(migrateMapSpec({ version: 3, layers: [] }));
    expect(result.ok).toBe(true);
    expect(result.originalVersion).toBe("1.0");
    expect(result.effectiveVersion).toBe("1.5");
    expect(result.disclosures[0]?.kind).toBe("invalid");
    expect(result.disclosures[0]?.detail).toContain("non-string version (number)");
  });

  it('version 为空字符串 "" → 视为缺失 → 缺省 1.0', () => {
    const result = expectMigratedOk(migrateMapSpec({ version: "", layers: [] }));
    expect(result.originalVersion).toBe("1.0");
    expect(result.disclosures[0]?.kind).toBe("invalid");
  });
});

describe("migrateMapSpec：forward version → fail-safe 拒绝", () => {
  it('version "2.0" → ok:false、reason forward_version、policy 披露', () => {
    const result = expectMigratedFail(migrateMapSpec({ version: "2.0", layers: [] }));
    expect(result.reason).toBe("forward_version");
    expect(result.version).toBe("2.0");
    expect(result.disclosures).toHaveLength(1);
    expect(result.disclosures[0]?.path).toBe("version");
    expect(result.disclosures[0]?.kind).toBe("policy");
    expect(result.disclosures[0]?.detail).toContain("newer than renderer-supported 1.5");
  });

  it('词表外的旧式版本 "1.6" 同样拒绝（未知 = forward fail-safe）', () => {
    const result = expectMigratedFail(migrateMapSpec({ version: "1.6", layers: [] }));
    expect(result.reason).toBe("forward_version");
    expect(result.version).toBe("1.6");
  });
});

describe("migrateMapSpec：invalid shape", () => {
  it("null 根本不是对象 → ok:false invalid_shape", () => {
    const result = expectMigratedFail(migrateMapSpec(null));
    expect(result.reason).toBe("invalid_shape");
    expect(result.version).toBe("");
    expect(result.disclosures[0]?.kind).toBe("invalid");
    expect(result.disclosures[0]?.detail).toContain("null");
  });

  it("数组根 → invalid_shape（渲染器无从协商）", () => {
    const result = expectMigratedFail(migrateMapSpec([{ version: "1.0" }]));
    expect(result.reason).toBe("invalid_shape");
    expect(result.disclosures[0]?.detail).toContain("array");
  });

  it("字符串根 → invalid_shape", () => {
    const result = expectMigratedFail(migrateMapSpec("1.0"));
    expect(result.reason).toBe("invalid_shape");
    expect(result.disclosures[0]?.detail).toContain("string");
  });
});

describe("migrateMapSpec：输入不可突变", () => {
  it("deepFreeze 输入后迁移不抛（structuredClone 深拷贝）", () => {
    const input = deepFreeze({
      version: "1.2",
      layers: [{ id: "frozen-layer", paint: { color: "#fff" } }],
    });
    expect(() => migrateMapSpec(input)).not.toThrow();
    const result = expectMigratedOk(migrateMapSpec(input));
    expect(result.spec.version).toBe("1.2");
    // 输入对象保持冻结、未被改写
    expect(Object.isFrozen(input)).toBe(true);
    expect(input.layers[0]?.id).toBe("frozen-layer");
  });
});

describe("checkComponentAbi：layout.composition 协商", () => {
  it("无 composition（undefined）→ abi_missing", () => {
    const result = checkComponentAbi(undefined);
    expect(result.status).toBe("abi_missing");
    if (result.status === "abi_missing") {
      expect(result.abiVersion).toBeNull();
      expect(result.detail).toContain("missing");
    }
  });

  it("composition 为 null → abi_missing", () => {
    expect(checkComponentAbi(null).status).toBe("abi_missing");
  });

  it("component_abi_version = 1（当前支持版）→ ok", () => {
    const result = checkComponentAbi({ component_abi_version: 1 });
    expect(result.status).toBe("ok");
    if (result.status === "ok") expect(result.abiVersion).toBe(1);
  });

  it("component_abi_version = 2 → abi_newer，detail 含 v2", () => {
    const result = checkComponentAbi({ component_abi_version: 2 });
    expect(result.status).toBe("abi_newer");
    if (result.status === "abi_newer") {
      expect(result.abiVersion).toBe(2);
      expect(result.detail).toContain("v2");
      expect(result.detail).toContain("layers unaffected");
    }
  });

  it("component_abi_version 非数字（字符串）→ abi_missing", () => {
    const result = checkComponentAbi({ component_abi_version: "1" });
    expect(result.status).toBe("abi_missing");
    if (result.status === "abi_missing") expect(result.abiVersion).toBeNull();
  });

  it("composition 携带无 component_abi_version 的对象 → abi_missing", () => {
    expect(checkComponentAbi({}).status).toBe("abi_missing");
  });
});
