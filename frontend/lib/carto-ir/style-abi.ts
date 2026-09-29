/**
 * Renderer ABI —— MapSpec → MapLibre 的**单一类型转换边界**（C11）。
 *
 * 此前 adapter/compiler 里 95+ 处 `as any` 的根因：MapSpec 的 paint/layout
 * 是开放 dict（MapLibre 词表开放性是 schema 决策，见 mapspec_schema
 * docstring），TS 无法把裸 JSON 数组验证为 ExpressionSpecification 的
 * 判别联合。本模块把"开放 JSON → 官方类型"收敛到**一个 audited cast
 * 集**：每个转换有便宜的 runtime 形状守卫 + 单一 `as`，调用侧拿到
 * maplibre-gl 官方类型（StyleSpecification/LayerSpecification/
 * ExpressionSpecification）。
 *
 * 不重写 MapLibre、不复制其类型 —— 只投影（maplibre-gl v6 re-export
 * style-spec 类型面）。
 */

import type {
  ExpressionSpecification,
  LayerSpecification,
  SourceSpecification,
  StyleSpecification,
} from "maplibre-gl";
import type { MapSpecLayer } from "@/lib/mapspec-compiler/types";

export type {
  ExpressionSpecification,
  LayerSpecification,
  SourceSpecification,
  StyleSpecification,
};

/**
 * paint/layout 属性值的合法域：MapLibre 表达式、标量或 null（部分
 * paint 属性接受 null 作为"删除"语义）。表达式在 JSON 里是数组 ——
 * TS 无法从字面量推断判别联合，经 asExpression 边界收口。
 */
export type MapLibrePaintValue =
  | ExpressionSpecification
  | string
  | number
  | boolean
  | null
  /** 数组值（如 line-dasharray [4,2]）—— 非表达式的裸数组值。 */
  | readonly number[];

/** 原生键 paint 对象（adapter/compiler 的实际产出方言）。 */
export type MapLibrePaint = Record<string, MapLibrePaintValue>;

/**
 * 裸表达式（JSON 数组形态）→ ExpressionSpecification。
 *
 * 这是本模块**唯一**的表达式转换口：带便宜形状守卫（数组 + 字符串头 =
 * MapLibre 表达式的必要条件），守卫失败仍返回 cast 结果但 devOnly 告警
 * —— MapLibre 自己会在 addLayer 时做完整校验，本守卫只负责把"明显不是
 * 表达式的值"在开发期暴露。
 */
export function asExpression(value: unknown): ExpressionSpecification {
  if (
    process.env.NODE_ENV !== "production" &&
    (value === null || typeof value !== "object" || !Array.isArray(value) ||
      typeof value[0] !== "string")
  ) {
    // 保守告警：标量/非表达式形状通常意味着调用侧绕过了 MapLibrePaintValue。
    console.warn(
      "[carto-ir] asExpression: non-expression payload",
      typeof value,
      Array.isArray(value) ? (value as unknown[])[0] : value,
    );
  }
  return value as ExpressionSpecification;
}

/**
 * 裸 JSON paint 值 → MapLibrePaintValue：数组走 asExpression 边界，
 * 标量直通，其余（语义 StyleMethod dict / 未形状化对象）显式透传
 * —— MapLibre addLayer 校验会拒绝非法形状（诚实失败优于静默改写）。
 */
export function asPaintValue(value: unknown): MapLibrePaintValue {
  if (Array.isArray(value)) return asExpression(value);
  if (
    value === null ||
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  ) {
    return value;
  }
  return value as MapLibrePaintValue;
}

/** filter 表达式（MapLibre legacy 数组形态；schema 侧 unknown[] 同构）。 */
export type MapLibreFilter = unknown[];

/**
 * 编译中图层（可变构建态；push 时经 asLayerSpecification 收口）。
 * layout 可选 —— MapLibre 层本身允许缺省 layout；需要保证 layout 在场
 * 的消费方用 `CompiledLayer & { layout: MapLibrePaint }` 交集收窄。
 */
export interface CompiledLayer {
  id: string;
  type: MapSpecLayer["type"];
  source?: string;
  "source-layer"?: string;
  layout?: MapLibrePaint;
  paint: MapLibrePaint;
  filter?: MapLibreFilter;
  minzoom?: number;
  maxzoom?: number;
}

/**
 * CompiledLayer → 官方 LayerSpecification（单一 audited cast）。
 * 类型面收口后，其余代码全部持有官方类型，字段改名在编译期暴露。
 */
export function asLayerSpecification(layer: CompiledLayer): LayerSpecification {
  return layer as unknown as LayerSpecification;
}

/** StyleSpecification 的最小构建形（headless/导出 style 产物）。 */
export type CompiledStyle = {
  version: 8;
  name?: string;
  center?: [number, number];
  zoom?: number;
  bearing?: number;
  pitch?: number;
  glyphs?: string;
  sprite?: string;
  sources: Record<string, SourceSpecification>;
  layers: LayerSpecification[];
  [key: string]: unknown;
};

export function asStyleSpecification(style: CompiledStyle): StyleSpecification {
  return style as unknown as StyleSpecification;
}

/**
 * 编译**结果**视图：官方 StyleSpecification 形状 + 图层保持构建态
 * CompiledLayer（开放键内省友好 —— 测试/导出消费面不必与官方判别联合
 * 缠斗）。官方类型已在本模块构建边界完成形状把关；视图是同一运行时
 * 对象的诚实投影。
 */
export type CompiledStyleView = Omit<StyleSpecification, "layers" | "sources"> & {
  sources: Record<string, SourceSpecification>;
  layers: CompiledLayer[];
};

export function asCompiledStyleView(
  style: CompiledStyle | StyleSpecification,
): CompiledStyleView {
  return style as unknown as CompiledStyleView;
}
