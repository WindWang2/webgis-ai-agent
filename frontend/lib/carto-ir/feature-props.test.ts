import { describe, it, expect } from "vitest";
import {
  FEATURE_ID_FIELDS,
  propOf,
  featureIdOf,
  type FeatureProperties,
} from "./feature-props";

/**
 * C11 要素属性 typed 访问面测试（issue #1556）。
 *
 * 锁定两个契约：
 *  - propOf 的值域守卫：GeoJSON 规范值域（string/number/boolean/null）
 *    透传；对象/数组/undefined 一律 undefined —— 绝不抛；
 *  - featureIdOf 的字段裁决顺序：id > OBJECTID > fid > FID（OBJECTID
 *    是 ESRI 导出链的既有事实缺省），全缺 → undefined。
 */

describe("FEATURE_ID_FIELDS 词表", () => {
  it("裁决顺序锁定（词表顺序即优先级）", () => {
    expect(FEATURE_ID_FIELDS).toEqual(["id", "OBJECTID", "fid", "FID"]);
  });
});

describe("propOf：typed 读取", () => {
  it("规范值域透传：string / number / boolean", () => {
    const feature = { properties: { name: "公园", OBJECTID: 42, active: false } };
    expect(propOf(feature, "name")).toBe("公园");
    expect(propOf(feature, "OBJECTID")).toBe(42);
    expect(propOf(feature, "active")).toBe(false);
  });

  it("null 值按规范语义透传（区别于缺键 undefined）", () => {
    const feature = { properties: { name: null } };
    expect(propOf(feature, "name")).toBeNull();
  });

  it("越界值域：对象值 → undefined（守卫，不抛）", () => {
    const feature = {
      properties: { shape: { nested: 1 } } as unknown as FeatureProperties,
    };
    expect(propOf(feature, "shape")).toBeUndefined();
  });

  it("越界值域：数组值 → undefined", () => {
    const feature = {
      properties: { tags: ["a", "b"] } as unknown as FeatureProperties,
    };
    expect(propOf(feature, "tags")).toBeUndefined();
  });

  it("缺键 → undefined", () => {
    const feature = { properties: { name: "x" } };
    expect(propOf(feature, "missing")).toBeUndefined();
  });

  it("properties 缺失 / 为 null → undefined", () => {
    expect(propOf({}, "id")).toBeUndefined();
    expect(propOf({ properties: null }, "id")).toBeUndefined();
  });

  it("feature 本身 undefined / null → undefined（绝不抛）", () => {
    expect(propOf(undefined, "id")).toBeUndefined();
    expect(propOf(null, "id")).toBeUndefined();
  });
});

describe("featureIdOf：标识字段单一裁决", () => {
  it("优先级 id > OBJECTID：多键并存时取 id", () => {
    const feature = { properties: { id: "feat-1", OBJECTID: 7, fid: "f", FID: "F" } };
    expect(featureIdOf(feature)).toBe("feat-1");
  });

  it("id 缺失 → 回落 OBJECTID（ESRI 导出链缺省）", () => {
    const feature = { properties: { OBJECTID: 7, fid: "f", FID: "F" } };
    expect(featureIdOf(feature)).toBe(7);
  });

  it("OBJECTID 缺失 → 回落 fid", () => {
    const feature = { properties: { fid: "f", FID: "F" } };
    expect(featureIdOf(feature)).toBe("f");
  });

  it("只剩 FID → 取 FID", () => {
    const feature = { properties: { FID: "F" } };
    expect(featureIdOf(feature)).toBe("F");
  });

  it("全缺 / feature 缺失 → undefined", () => {
    expect(featureIdOf({ properties: { name: "x" } })).toBeUndefined();
    expect(featureIdOf({})).toBeUndefined();
    expect(featureIdOf(undefined)).toBeUndefined();
    expect(featureIdOf(null)).toBeUndefined();
  });

  it("id 为 null → 回落下一候选（null = 字段存在但无值）", () => {
    // featureIdOf 裁决判据 `v != null`：null 视为"无值"，与 use-feature-
    // selection 的 `??` 链语义一致（S2 初版锁定过 `!== undefined` 旧语义，
    // 主会话 review 判定为 quirk 并修正 —— null 必须回落 OBJECTID）。
    const feature = { properties: { id: null, OBJECTID: 9 } };
    expect(featureIdOf(feature)).toBe(9);
  });
});
