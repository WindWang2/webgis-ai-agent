/**
 * scene3d/geo — 纯坐标/矩阵数学（G3D02 M3）。
 *
 * 全部为无 WebGL、无 MapLibre 依赖的纯函数（jsdom 可测）。投影公式与
 * MapLibre `MercatorCoordinate`（maplibre-gl-shared mercator_utils.ts）
 * **逐字一致**：地球半径 6371008.8、mercator 世界域 [0,1]、z = 海拔 /
 * 纬度周长。任何偏差都会让模型漂移/悬浮 —— 常量与公式不得"就地改良"。
 *
 * 轴系约定（显式、不猜）：
 * - mercator 世界：x 东、y 南（Web Mercator 惯例）、z 天。
 * - 局部 ENU（放置语义）：东-北-天，米制。
 * - glTF 依规范 y-up（+Z 向观察者）；3D Tiles 依规范 z-up ENU。
 *   各格式到 ENU 的校正固定在 `gltfToEnuFix()` / 恒等，无配置位。
 */

/** MapLibre 同源地球半径（米）。 */
export const EARTH_RADIUS_M = 6371008.8;
const EARTH_CIRCUMFERENCE_M = 2 * Math.PI * EARTH_RADIUS_M;
const DEG = Math.PI / 180;

export function mercatorXfromLng(lng: number): number {
  return (180 + lng) / 360;
}

export function mercatorYfromLat(lat: number): number {
  return (180 - (180 / Math.PI) * Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360))) / 360;
}

export function circumferenceAtLatitude(lat: number): number {
  return EARTH_CIRCUMFERENCE_M * Math.cos(lat * DEG);
}

/** z（世界单位）= 海拔米 / 纬度周长。 */
export function mercatorZfromAltitude(altitudeM: number, lat: number): number {
  return altitudeM / circumferenceAtLatitude(lat);
}

/** 1 米在该纬度的 mercator 世界单位数（局部各向同性）。 */
export function meterInMercatorUnit(lat: number): number {
  return 1 / circumferenceAtLatitude(lat);
}

export function lngFromMercatorX(x: number): number {
  return x * 360 - 180;
}

export function latFromMercatorY(y: number): number {
  const y2 = 180 - y * 360;
  return (360 / Math.PI) * Math.atan(Math.exp((y2 * Math.PI) / 180)) - 90;
}

export interface WorldCoord {
  x: number;
  y: number;
  z: number;
}

export function lngLatAltToWorld(lng: number, lat: number, altitudeM: number): WorldCoord {
  return {
    x: mercatorXfromLng(lng),
    y: mercatorYfromLat(lat),
    z: mercatorZfromAltitude(altitudeM, lat),
  };
}

export function worldToLngLatAlt(w: WorldCoord): { lng: number; lat: number; altitudeM: number } {
  const lat = latFromMercatorY(w.y);
  return {
    lng: lngFromMercatorX(w.x),
    lat,
    altitudeM: w.z * circumferenceAtLatitude(lat),
  };
}

// ── 4×4 矩阵（列主序，与 MapLibre gl-matrix / THREE.Matrix4 元素布局一致）──

export type Mat4 = number[]; // length 16, column-major

export function identityMat4(): Mat4 {
  return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
}

export function multiplyMat4(a: Mat4, b: Mat4): Mat4 {
  const out = new Array<number>(16).fill(0);
  for (let c = 0; c < 4; c++) {
    for (let r = 0; r < 4; r++) {
      let s = 0;
      for (let k = 0; k < 4; k++) s += a[k * 4 + r] * b[c * 4 + k];
      out[c * 4 + r] = s;
    }
  }
  return out;
}

export function translationMat4(tx: number, ty: number, tz: number): Mat4 {
  const m = identityMat4();
  m[12] = tx;
  m[13] = ty;
  m[14] = tz;
  return m;
}

export function scaleMat4(s: number): Mat4 {
  const m = identityMat4();
  m[0] = s;
  m[5] = s;
  m[10] = s;
  return m;
}

/** 绕轴旋转（axis: 0=x, 1=y, 2=z；右手系，弧度）。 */
export function rotationAxisMat4(axis: 0 | 1 | 2, rad: number): Mat4 {
  const c = Math.cos(rad);
  const s = Math.sin(rad);
  const m = identityMat4();
  if (axis === 0) {
    m[5] = c; m[6] = s; m[9] = -s; m[10] = c;
  } else if (axis === 1) {
    m[0] = c; m[2] = -s; m[8] = s; m[10] = c;
  } else {
    m[0] = c; m[1] = s; m[4] = -s; m[5] = c;
  }
  return m;
}

export function transformPoint(m: Mat4, p: WorldCoord): WorldCoord {
  const x = m[0] * p.x + m[4] * p.y + m[8] * p.z + m[12];
  const y = m[1] * p.x + m[5] * p.y + m[9] * p.z + m[13];
  const z = m[2] * p.x + m[6] * p.y + m[10] * p.z + m[14];
  return { x, y, z };
}

/** 4×4 逆（通用伴随法；奇异矩阵 → null，不抛）。 */
export function invertMat4(m: Mat4): Mat4 | null {
  const a00 = m[0], a01 = m[1], a02 = m[2], a03 = m[3];
  const a10 = m[4], a11 = m[5], a12 = m[6], a13 = m[7];
  const a20 = m[8], a21 = m[9], a22 = m[10], a23 = m[11];
  const a30 = m[12], a31 = m[13], a32 = m[14], a33 = m[15];

  const b00 = a00 * a11 - a01 * a10;
  const b01 = a00 * a12 - a02 * a10;
  const b02 = a00 * a13 - a03 * a10;
  const b03 = a01 * a12 - a02 * a11;
  const b04 = a01 * a13 - a03 * a11;
  const b05 = a02 * a13 - a03 * a12;
  const b06 = a20 * a31 - a21 * a30;
  const b07 = a20 * a32 - a22 * a30;
  const b08 = a20 * a33 - a23 * a30;
  const b09 = a21 * a32 - a22 * a31;
  const b10 = a21 * a33 - a23 * a31;
  const b11 = a22 * a33 - a23 * a32;

  let det = b00 * b11 - b01 * b10 + b02 * b09 + b03 * b08 - b04 * b07 + b05 * b06;
  if (!Number.isFinite(det) || det === 0) return null;
  det = 1.0 / det;

  return [
    (a11 * b11 - a12 * b10 + a13 * b09) * det,
    (a02 * b10 - a01 * b11 - a03 * b09) * det,
    (a31 * b05 - a32 * b04 + a33 * b03) * det,
    (a22 * b04 - a21 * b05 - a23 * b03) * det,
    (a12 * b08 - a10 * b11 - a13 * b07) * det,
    (a00 * b11 - a02 * b08 + a03 * b07) * det,
    (a32 * b02 - a30 * b05 - a33 * b01) * det,
    (a20 * b05 - a22 * b02 + a23 * b01) * det,
    (a10 * b10 - a11 * b08 + a13 * b06) * det,
    (a01 * b08 - a00 * b10 - a03 * b06) * det,
    (a30 * b04 - a31 * b02 + a33 * b00) * det,
    (a21 * b02 - a20 * b04 - a23 * b00) * det,
    (a11 * b07 - a10 * b09 - a12 * b06) * det,
    (a00 * b09 - a01 * b07 + a02 * b06) * det,
    (a31 * b01 - a30 * b03 - a32 * b00) * det,
    (a20 * b03 - a21 * b01 + a22 * b00) * det,
  ];
}

export interface FrustumPlane {
  /** 单位外法线。 */
  normal: WorldCoord;
  /** 平面偏移（n·x + d ≥ 0 在视锥内）。 */
  d: number;
}

/** Gribb–Hartmann：列主序 world→clip 矩阵 → 6 视锥平面（world 空间）。 */
export function frustumPlanesFromMat4(m: Mat4): FrustumPlane[] {
  // 行向量提取（列主序 m：m[col*4+row]）
  const row = (r: number) => [m[r], m[4 + r], m[8 + r], m[12 + r]];
  const r0 = row(0), r1 = row(1), r2 = row(2), r3 = row(3);
  const combos: [number[], number[]][] = [
    [r3, r0], // left:  r3 + r0
    [r3, r0], // right: r3 - r0（符号在 combine 处理）
    [r3, r1], // bottom / top
    [r3, r1],
    [r3, r2], // near / far
    [r3, r2],
  ];
  const signs = [1, -1, 1, -1, 1, -1];
  const planes: FrustumPlane[] = [];
  for (let i = 0; i < 6; i++) {
    const [a, b] = combos[i];
    const s = signs[i];
    let nx = a[0] + s * b[0];
    let ny = a[1] + s * b[1];
    let nz = a[2] + s * b[2];
    let d = a[3] + s * b[3];
    const len = Math.hypot(nx, ny, nz) || 1;
    nx /= len; ny /= len; nz /= len; d /= len;
    planes.push({ normal: { x: nx, y: ny, z: nz }, d });
  }
  return planes;
}

/**
 * 视锥平面从 world 空间变换到模型局部空间（localToWorld = local→world）。
 * 平面按齐次协变变换：p_local = (M⁻ᵀ)·p_world。
 */
export function planeToLocalSpace(
  plane: FrustumPlane,
  localToWorld: Mat4,
): FrustumPlane {
  const inv = invertMat4(localToWorld);
  if (!inv) return plane;
  // inv 的转置作用于平面向量（nx, ny, nz, d）
  const nx = inv[0] * plane.normal.x + inv[4] * plane.normal.y + inv[8] * plane.normal.z;
  const ny = inv[1] * plane.normal.x + inv[5] * plane.normal.y + inv[9] * plane.normal.z;
  const nz = inv[2] * plane.normal.x + inv[6] * plane.normal.y + inv[10] * plane.normal.z;
  const d = inv[3] * plane.normal.x + inv[7] * plane.normal.y + inv[11] * plane.normal.z + inv[15] * plane.d;
  const len = Math.hypot(nx, ny, nz) || 1;
  return { normal: { x: nx / len, y: ny / len, z: nz / len }, d: d / len };
}

// ── 放置矩阵 ─────────────────────────────────────────────────────────────

export interface AssetPlacementInput {
  /** [lng, lat]（WGS84 度）。 */
  anchor: readonly [number, number];
  /** 锚点海拔（米；datum 由 spec vertical 声明承载，数学上一致）。 */
  altitudeM?: number;
  /** ENU 航向（度；0 = 北，顺时针为正）。 */
  headingDeg?: number;
  /** ENU 俯仰（度；绕东轴，抬升为正）。 */
  pitchDeg?: number;
  /** ENU 翻滚（度；绕天轴）。 */
  rollDeg?: number;
  /** 米制缩放因子（>0）。 */
  scale?: number;
}

/**
 * glTF 模型轴系 → ENU 校正（规范固定：glTF +Y up、+Z 前向观察者；
 * 等价 Cesium 的 Axis.Y_UP_TO_Z_UP = 绕 X 轴 +90°）：X→E、Y→U、Z→−N。
 */
export function gltfToEnuFix(): Mat4 {
  // 列 = 模型基向量的 ENU 像：X_model→E=(1,0,0)、Y_model→U=(0,0,1)、
  // Z_model→南=(0,−1,0)。
  const m = identityMat4();
  m[0] = 1; m[1] = 0; m[2] = 0;   // 列0 = E
  m[4] = 0; m[5] = 0; m[6] = 1;   // 列1 = U
  m[8] = 0; m[9] = -1; m[10] = 0; // 列2 = −N
  return m;
}

/**
 * ENU 局部坐标（米）→ mercator 世界的基变换（无平移/缩放）：
 * E→(1,0,0)、N→(0,−1,0)（y 南）、U→(0,0,1)。
 */
export function enuToMercatorAxes(): Mat4 {
  const m = identityMat4();
  m[5] = -1; // N → −y
  m[10] = 1;
  return m;
}

/**
 * 放置矩阵：模型局部（米）→ mercator 世界。
 *
 * M = T(anchor_world) · R_enuToMerc · R_enu(heading,pitch,roll) · F_axes · S
 * 其中 F_axes = gltfToEnuFix()（glTF）或 I（3D Tiles，本就 z-up ENU）。
 */
export function buildPlacementMatrix(
  placement: AssetPlacementInput,
  opts: { format: 'gltf' | 'glb' | '3dtiles' },
): Mat4 {
  const [lng, lat] = placement.anchor;
  const anchorW = lngLatAltToWorld(lng, lat, placement.altitudeM ?? 0);
  const metersPerUnit = meterInMercatorUnit(lat);
  const scale = (placement.scale ?? 1) * metersPerUnit;

  // ENU 姿态（heading 绕 U，顺时针为正 = 绕 U 负向右手）。
  const heading = -(placement.headingDeg ?? 0) * DEG;
  const pitch = (placement.pitchDeg ?? 0) * DEG;
  const roll = (placement.rollDeg ?? 0) * DEG;
  // R_enu = Ry? 统一以 ENU 轴（E=x, N=y, U=z）构造：
  // heading 绕 U(z)、pitch 绕 E(x)、roll 绕 N(y)。
  const r_enu = multiplyMat4(
    multiplyMat4(rotationAxisMat4(2, heading), rotationAxisMat4(0, pitch)),
    rotationAxisMat4(1, roll),
  );
  const axesFix = opts.format === '3dtiles' ? identityMat4() : gltfToEnuFix();

  // mercator 世界里的"米"标尺随纬度（E/N/U 同尺度 —— 局部各向同性）。
  return multiplyMat4(
    translationMat4(anchorW.x, anchorW.y, anchorW.z),
    multiplyMat4(
      enuToMercatorAxes(),
      multiplyMat4(r_enu, multiplyMat4(axesFix, scaleMat4(scale))),
    ),
  );
}

// ── WGS84 ECEF ↔ ENU（3D Tiles root transform 消费）─────────────────────

const WGS84_A = 6378137.0;
const WGS84_E2 = (WGS84_A * WGS84_A - 6356752.3142451793 * 6356752.3142451793) / (WGS84_A * WGS84_A);

/** 大地纬度/经度（度）→ ECEF 单位向量构成的 ENU 旋转（ENU→ECEF，列主序）。 */
export function enuFromEcefRotation(latDeg: number, lngDeg: number): Mat4 {
  const lat = latDeg * DEG;
  const lon = lngDeg * DEG;
  const sl = Math.sin(lat), cl = Math.cos(lat);
  const so = Math.sin(lon), co = Math.cos(lon);
  // E = (−sinλ, cosλ, 0); N = (−sinφcosλ, −sinφsinλ, cosφ); U = (cosφcosλ, cosφsinλ, sinφ)
  // ECEF 是右手系（x 指 0°经线、z 指北极）；该矩阵将 ENU 向量旋转到 ECEF。
  const m = identityMat4();
  m[0] = -so;       m[1] = -sl * co;   m[2] = cl * co;
  m[4] = co;        m[5] = -sl * so;   m[6] = cl * so;
  m[8] = 0;         m[9] = cl;         m[10] = sl;
  return m;
}

/** 地心大地坐标 → ECEF（米）。 */
export function geodeticToEcef(latDeg: number, lngDeg: number, heightM: number): WorldCoord {
  const lat = latDeg * DEG;
  const lon = lngDeg * DEG;
  const sl = Math.sin(lat), cl = Math.cos(lat);
  const N = WGS84_A / Math.sqrt(1 - WGS84_E2 * sl * sl);
  return {
    x: (N + heightM) * cl * Math.cos(lon),
    y: (N + heightM) * cl * Math.sin(lon),
    z: (N * (1 - WGS84_E2) + heightM) * sl,
  };
}

// ── SSE（屏幕空间误差）───────────────────────────────────────────────────

/**
 * 3D Tiles 标准 SSE：`(geometricError × viewportHeight) / (distance × 2·tan(fov/2))`。
 * distance 为相机到包围球表面的最近距离（钳 ≥1e-6 防除零）。
 */
export function screenSpaceError(params: {
  geometricError: number;
  distanceM: number;
  viewportHeightPx: number;
  fovRad: number;
}): number {
  const denom = Math.max(params.distanceM, 1e-6) * 2 * Math.tan(params.fovRad / 2);
  return (params.geometricError * params.viewportHeightPx) / Math.max(denom, 1e-9);
}

/** 相机到包围球最近距离（米）。 */
export function distanceToSphereM(
  cameraWorld: WorldCoord,
  sphere: { center: WorldCoord; radiusM: number },
  metersPerWorldUnit: number,
): number {
  const dx = cameraWorld.x - sphere.center.x;
  const dy = cameraWorld.y - sphere.center.y;
  const dz = cameraWorld.z - sphere.center.z;
  const distWorld = Math.sqrt(dx * dx + dy * dy + dz * dz);
  return Math.max(distWorld * metersPerWorldUnit - sphere.radiusM, 0.001);
}
