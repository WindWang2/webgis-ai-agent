/**
 * canonical.ts —— 图符域确定性序列化底座（Python canonical_json / hashlib.sha256
 * / math.fsum 的纯 TS 孪生，零依赖）。
 *
 * 被 oracle.ts（geometry_digest）与 layout.ts（layout digest）共用。
 *
 * 与 Python 的对齐口径（contracts.canonical_json = json.dumps(
 * sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)）：
 * - 键序：递归按字典序排序（本域对象键均为 ASCII 标识符，与 Python 的
 *   code-point 排序一致；含增补平面字符的键序与 Python 可能不同 —— 已知偏差）。
 * - 字符串：JSON.stringify 输出与 Python ensure_ascii=False 逐位一致（本域
 *   字符串不含 U+2028/U+2029 —— JS 会转义而 Python 不转义，已知偏差）。
 * - 浮点：Python repr(float) 是最短往返表示，整数位值追加 ".0"；JS 的
 *   String(double) 同为最短往返，两者在常规量级逐位一致，仅指数格式差异
 *   （JS "1e-7" vs Python "1e-07"，由 fixExponent 归一；≥1e21 两者一致）。
 *   本域数值均经 3 位小数量化 / 9 位坐标量化，不会触及指数区。
 * - int / float 的区分：JS number 不分整浮，用 pyInt / pyFloat 显式标签
 *   （digest 输入由调用方按 Python dict 的实际类型打标签）。
 * - allow_nan=False：非有限浮点直接拒绝（TypeError）。
 */

const SHA256_K: readonly number[] = [
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
];

/**
 * 同步纯 TS sha256（FIPS 180-4），输入按 UTF-8 编码，
 * 等价 hashlib.sha256(s.encode("utf-8")).hexdigest()。
 */
export function sha256Hex(input: string): string {
  const bytes = new TextEncoder().encode(input);
  const bitLen = bytes.length * 8;
  const paddedLen = (((bytes.length + 8) >> 6) + 1) << 6;
  const buf = new Uint8Array(paddedLen);
  buf.set(bytes);
  buf[bytes.length] = 0x80;
  const view = new DataView(buf.buffer);
  view.setUint32(paddedLen - 8, Math.floor(bitLen / 4294967296), false);
  view.setUint32(paddedLen - 4, bitLen % 4294967296, false);

  let h0 = 0x6a09e667, h1 = 0xbb67ae85, h2 = 0x3c6ef372, h3 = 0xa54ff53a;
  let h4 = 0x510e527f, h5 = 0x9b05688c, h6 = 0x1f83d9ab, h7 = 0x5be0cd19;
  const w = new Uint32Array(64);
  for (let off = 0; off < paddedLen; off += 64) {
    for (let i = 0; i < 16; i++) w[i] = view.getUint32(off + i * 4, false);
    for (let i = 16; i < 64; i++) {
      const x15 = w[i - 15];
      const x2 = w[i - 2];
      const s0 =
        (((x15 >>> 7) | (x15 << 25)) ^ ((x15 >>> 18) | (x15 << 14)) ^ (x15 >>> 3)) >>> 0;
      const s1 =
        (((x2 >>> 17) | (x2 << 15)) ^ ((x2 >>> 19) | (x2 << 13)) ^ (x2 >>> 10)) >>> 0;
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
    }
    let a = h0, b = h1, c = h2, d = h3, e = h4, f = h5, g = h6, hh = h7;
    for (let i = 0; i < 64; i++) {
      const S1 =
        (((e >>> 6) | (e << 26)) ^ ((e >>> 11) | (e << 21)) ^ ((e >>> 25) | (e << 7))) >>> 0;
      const ch = ((e & f) ^ (~e & g)) >>> 0;
      const t1 = (hh + S1 + ch + SHA256_K[i] + w[i]) >>> 0;
      const S0 =
        (((a >>> 2) | (a << 30)) ^ ((a >>> 13) | (a << 19)) ^ ((a >>> 22) | (a << 10))) >>> 0;
      const maj = ((a & b) ^ (a & c) ^ (b & c)) >>> 0;
      const t2 = (S0 + maj) >>> 0;
      hh = g; g = f; f = e; e = (d + t1) >>> 0;
      d = c; c = b; b = a; a = (t1 + t2) >>> 0;
    }
    h0 = (h0 + a) >>> 0; h1 = (h1 + b) >>> 0; h2 = (h2 + c) >>> 0; h3 = (h3 + d) >>> 0;
    h4 = (h4 + e) >>> 0; h5 = (h5 + f) >>> 0; h6 = (h6 + g) >>> 0; h7 = (h7 + hh) >>> 0;
  }
  return [h0, h1, h2, h3, h4, h5, h6, h7]
    .map((v) => v.toString(16).padStart(8, "0"))
    .join("");
}

/** Python int 标签（digest 输入中的整型字段：计数、rank、min_zoom 等）。 */
export interface PyInt {
  readonly __pyNum: "int";
  readonly value: number;
}

/** Python float 标签（digest 输入中的浮点字段：坐标、角度、px 等）。 */
export interface PyFloat {
  readonly __pyNum: "float";
  readonly value: number;
}

export type CanonicalValue =
  | null
  | boolean
  | string
  | PyInt
  | PyFloat
  | readonly CanonicalValue[]
  | { readonly [key: string]: CanonicalValue };

export function pyInt(value: number): PyInt {
  if (!Number.isInteger(value)) {
    throw new TypeError(`pyInt 要求整数，got ${value}`);
  }
  return { __pyNum: "int", value };
}

export function pyFloat(value: number): PyFloat {
  // 对齐 allow_nan=False：NaN/Inf 在 Python 里 json.dumps 直接抛 ValueError。
  if (!Number.isFinite(value)) {
    throw new TypeError(`pyFloat 拒绝非有限值（allow_nan=False），got ${value}`);
  }
  return { __pyNum: "float", value };
}

/** JS "1e-7" → Python "1e-07"（指数位补零）。 */
function fixExponent(s: string): string {
  return s.replace(/e([+-])(\d+)$/, (_m, sign: string, digits: string) =>
    `e${sign}${digits.length < 2 ? "0" + digits : digits}`,
  );
}

/** Python repr(float) 等价输出：整位数值补 ".0"；指数格式归一。 */
function pyFloatRepr(value: number): string {
  const s = String(value);
  if (s.includes("e")) return fixExponent(s);
  if (s.includes("E")) return fixExponent(s.replace("E", "e"));
  if (Number.isInteger(value)) return `${s}.0`;
  return s;
}

function isPyNum(v: CanonicalValue): v is PyInt | PyFloat {
  return typeof v === "object" && v !== null && !Array.isArray(v) && "__pyNum" in v;
}

function serialize(value: CanonicalValue, out: string[]): void {
  if (value === null) {
    out.push("null");
    return;
  }
  if (typeof value === "boolean") {
    out.push(value ? "true" : "false");
    return;
  }
  if (typeof value === "string") {
    out.push(JSON.stringify(value));
    return;
  }
  if (isPyNum(value)) {
    if (value.__pyNum === "int") {
      out.push(String(value.value));
    } else {
      out.push(pyFloatRepr(value.value));
    }
    return;
  }
  if (Array.isArray(value)) {
    out.push("[");
    for (let i = 0; i < value.length; i++) {
      if (i > 0) out.push(",");
      serialize(value[i], out);
    }
    out.push("]");
    return;
  }
  // 对象：键按字典序（本域键均为 ASCII —— 与 Python sort_keys 一致）。
  const obj = value as { readonly [key: string]: CanonicalValue };
  out.push("{");
  const keys = Object.keys(obj).sort();
  let first = true;
  for (const key of keys) {
    const item = obj[key];
    if (item === undefined) continue;
    if (!first) out.push(",");
    first = false;
    out.push(JSON.stringify(key), ":");
    serialize(item, out);
  }
  out.push("}");
}

/** Python json.dumps(sort_keys=True, separators=(",",":"), ensure_ascii=False) 等价。 */
export function canonicalJson(value: CanonicalValue): string {
  const out: string[] = [];
  serialize(value, out);
  return out.join("");
}

/**
 * ``math.fsum`` 的 TS 位级孪生 —— CPython 3.12 ``math_fsum``（msum4 算法）
 * 忠实端口：partials 精确展开 + ``sum_exact`` 终折（从大到小回加，遇到
 * 不精确即停，再做跨 partial 的 half-even 修正）。简单逆序求和在部分
 * 输入上与 math.fsum 相差 1 ulp（随机差分语料实测），故逐字移植。
 */
export function fsum(values: readonly number[]): number {
  const partials: number[] = [];
  for (const xsave of values) {
    let x = xsave;
    let i = 0;
    for (let j = 0; j < partials.length; j++) {
      let y = partials[j];
      if (Math.abs(x) < Math.abs(y)) {
        const t = x;
        x = y;
        y = t;
      }
      const hi = x + y;
      const yr = hi - x;
      const lo = y - yr;
      if (lo !== 0.0) {
        partials[i] = lo;
        i += 1;
      }
      x = hi;
    }
    partials.length = i;
    if (x !== 0.0) partials.push(x); // CPython: ps[i:] = [x] 仅当 x != 0
  }
  // sum_exact：从最大量级 partial 开始回加，一旦 lo != 0（和变不精确）即停；
  // 再用 lo 的符号与下一个 partial 同号性做 half-even 修正（保证交换律）。
  let hi = 0.0;
  let lo = 0.0;
  let n = partials.length;
  if (n > 0) {
    hi = partials[--n];
    while (n > 0) {
      const x = hi;
      const y = partials[--n];
      hi = x + y;
      const yr = hi - x;
      lo = y - yr;
      if (lo !== 0.0) break;
    }
    if (
      n > 0 &&
      ((lo < 0.0 && partials[n - 1] < 0.0) || (lo > 0.0 && partials[n - 1] > 0.0))
    ) {
      const y2 = lo * 2.0;
      const x2 = hi + y2;
      const yr2 = x2 - hi;
      if (y2 === yr2) hi = x2;
    }
  }
  return hi;
}
