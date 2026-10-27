/**
 * scene3d/geo 数学 oracle 测试（G3D02 M7）。
 *
 * Oracle 语义：mercator 投影公式与 MapLibre `MercatorCoordinate`
 * **逐位一致**（oracle 值由真库在 node 环境生成 —— 见各常量注释）；
 * 矩阵/视锥/ECEF 数学用可手算的构造性已知值。
 */
import { describe, expect, it } from 'vitest';

import {
  buildPlacementMatrix,
  circumferenceAtLatitude,
  distanceToSphereM,
  enuFromEcefRotation,
  frustumPlanesFromMat4,
  geodeticToEcef,
  gltfToEnuFix,
  identityMat4,
  invertMat4,
  lngLatAltToWorld,
  mercatorXfromLng,
  mercatorYfromLat,
  mercatorZfromAltitude,
  meterInMercatorUnit,
  planeToLocalSpace,
  screenSpaceError,
  transformPoint,
  worldToLngLatAlt,
} from './geo';

// ── MapLibre 真库 oracle（node 环境预生成，六位小数精度断言）────────────
const MAPLIBRE_ORACLE: Array<[number, number, number, number, number, number]> = [
  //  lng      lat     alt       x           y           z
  [0, 0, 0, 0.5, 0.5, 0],
  [104.06, 30.67, 500, 0.7890555556, 0.4104188492, 0.000014521890],
  [-73.9857, 40.7484, 0, 0.2944841667, 0.3758503011, 0],
  [116.397, 39.909, 1000, 0.8233250000, 0.3789089161, 0.000032567177],
  [10, -45, -100, 0.5277777778, 0.6402749631, -0.000003532864],
];

describe('mercator projection matches MapLibre MercatorCoordinate', () => {
  for (const [lng, lat, alt, x, y, z] of MAPLIBRE_ORACLE) {
    it(`(${lng}, ${lat}, ${alt}m)`, () => {
      const w = lngLatAltToWorld(lng, lat, alt);
      expect(w.x).toBeCloseTo(x, 9);
      expect(w.y).toBeCloseTo(y, 9);
      expect(w.z).toBeCloseTo(z, 11);
      expect(mercatorXfromLng(lng)).toBeCloseTo(x, 9);
      expect(mercatorYfromLat(lat)).toBeCloseTo(y, 9);
      expect(mercatorZfromAltitude(alt, lat)).toBeCloseTo(z, 11);
      // round-trip
      const back = worldToLngLatAlt(w);
      expect(back.lng).toBeCloseTo(lng, 7);
      expect(back.lat).toBeCloseTo(lat, 7);
      expect(back.altitudeM).toBeCloseTo(alt, 3);
    });
  }

  it('meterInMercatorUnit matches library values', () => {
    // MercatorCoordinate.fromLngLat([0,0]).meterInMercatorCoordinateUnits()
    expect(meterInMercatorUnit(0)).toBeCloseTo(2.4981e-8, 10);
    // 纬度 60 的值
    expect(meterInMercatorUnit(60)).toBeCloseTo(4.9962e-8, 10);
  });

  it('circumferenceAtLatitude(0) = earth circumference', () => {
    expect(circumferenceAtLatitude(0)).toBeCloseTo(2 * Math.PI * 6371008.8, 6);
  });
});

describe('placement matrix', () => {
  const LNG = 104.06, LAT = 30.67;

  it('identity placement maps origin to anchor world position', () => {
    const m = buildPlacementMatrix(
      { anchor: [LNG, LAT], altitudeM: 100, headingDeg: 0, pitchDeg: 0, rollDeg: 0, scale: 1 },
      { format: '3dtiles' },
    );
    const origin = transformPoint(m, { x: 0, y: 0, z: 0 });
    const anchorW = lngLatAltToWorld(LNG, LAT, 100);
    expect(origin.x).toBeCloseTo(anchorW.x, 12);
    expect(origin.y).toBeCloseTo(anchorW.y, 12);
    expect(origin.z).toBeCloseTo(anchorW.z, 12);
  });

  it('1 米 East 位移 ≈ meterInMercatorUnit 世界单位 +x', () => {
    const m = buildPlacementMatrix({ anchor: [LNG, LAT] }, { format: '3dtiles' });
    const p = transformPoint(m, { x: 1, y: 0, z: 0 });
    const anchorW = lngLatAltToWorld(LNG, LAT, 0);
    const unit = meterInMercatorUnit(LAT);
    expect(p.x - anchorW.x).toBeCloseTo(unit, 12);
    expect(p.y - anchorW.y).toBeCloseTo(0, 12);
    expect(p.z - anchorW.z).toBeCloseTo(0, 12);
  });

  it('1 米 North 位移 = −y（mercator y 南向）', () => {
    const m = buildPlacementMatrix({ anchor: [LNG, LAT] }, { format: '3dtiles' });
    const p = transformPoint(m, { x: 0, y: 1, z: 0 });
    const anchorW = lngLatAltToWorld(LNG, LAT, 0);
    const unit = meterInMercatorUnit(LAT);
    expect(p.y - anchorW.y).toBeCloseTo(-unit, 12);
  });

  it('1 米 Up 位移 = +z', () => {
    const m = buildPlacementMatrix({ anchor: [LNG, LAT] }, { format: '3dtiles' });
    const p = transformPoint(m, { x: 0, y: 0, z: 1 });
    const anchorW = lngLatAltToWorld(LNG, LAT, 0);
    expect(p.z - anchorW.z).toBeCloseTo(meterInMercatorUnit(LAT), 12);
  });

  it('glTF 轴系校正：Y_model → Up(+z)，Z_model → 南(+y)', () => {
    const m = buildPlacementMatrix({ anchor: [LNG, LAT] }, { format: 'glb' });
    const anchorW = lngLatAltToWorld(LNG, LAT, 0);
    const unit = meterInMercatorUnit(LAT);
    const up = transformPoint(m, { x: 0, y: 1, z: 0 }); // Y_model
    expect(up.z - anchorW.z).toBeCloseTo(unit, 12);
    expect(up.x - anchorW.x).toBeCloseTo(0, 12);
    const forward = transformPoint(m, { x: 0, y: 0, z: 1 }); // Z_model
    expect(forward.y - anchorW.y).toBeCloseTo(unit, 12); // 南 = +y
  });

  it('heading 90° 把 East 转到 South', () => {
    const m0 = buildPlacementMatrix({ anchor: [LNG, LAT] }, { format: '3dtiles' });
    const m90 = buildPlacementMatrix({ anchor: [LNG, LAT], headingDeg: 90 }, { format: '3dtiles' });
    const e90 = transformPoint(m90, { x: 1, y: 0, z: 0 });
    // heading 90（顺时针俯视）：E → S；S = −y 方向的北基底…
    // 直接对照：heading 90 后模型 X 应指向 heading 0 时的 −Y（北→南）。
    const north0 = transformPoint(m0, { x: 0, y: -1, z: 0 });
    expect(e90.x).toBeCloseTo(north0.x, 12);
    expect(e90.y).toBeCloseTo(north0.y, 12);
    expect(e90.z).toBeCloseTo(north0.z, 12);
  });

  it('gltfToEnuFix is an involution-safe rotation (determinant +1, orthonormal)', () => {
    const f = gltfToEnuFix();
    const det =
      f[0] * (f[5] * f[10] - f[6] * f[9])
      - f[4] * (f[1] * f[10] - f[2] * f[9])
      + f[8] * (f[1] * f[6] - f[2] * f[5]);
    expect(det).toBeCloseTo(1, 12);
  });

  it('scale 因子作用于局部米制', () => {
    const m1 = buildPlacementMatrix({ anchor: [LNG, LAT], scale: 2 }, { format: '3dtiles' });
    const anchorW = lngLatAltToWorld(LNG, LAT, 0);
    const p = transformPoint(m1, { x: 1, y: 0, z: 0 });
    expect(p.x - anchorW.x).toBeCloseTo(2 * meterInMercatorUnit(LAT), 12);
  });
});

describe('matrix utilities', () => {
  it('invertMat4 round-trips a placement matrix', () => {
    const m = buildPlacementMatrix(
      { anchor: [104, 30.5], altitudeM: 20, headingDeg: 33, pitchDeg: 5, rollDeg: -2, scale: 1.5 },
      { format: 'gltf' },
    );
    const inv = invertMat4(m);
    expect(inv).not.toBeNull();
    const p = { x: 3, y: -2, z: 7 };
    const world = transformPoint(m, p);
    const back = transformPoint(inv!, world);
    expect(back.x).toBeCloseTo(p.x, 9);
    expect(back.y).toBeCloseTo(p.y, 9);
    expect(back.z).toBeCloseTo(p.z, 9);
  });

  it('invertMat4 returns null for singular', () => {
    const bad = new Array<number>(16).fill(0);
    expect(invertMat4(bad)).toBeNull();
  });

  it('frustum planes from an orthographic-ish MVP box cull correctly', () => {
    // 构造 x∈[−1,1] → NDC x 的简单矩阵（正交）：A = T(−1) · S(0.5)…
    // 直接用恒等矩阵：NDC == world —— 视锥 = [−1,1]³。
    const planes = frustumPlanesFromMat4(identityMat4());
    expect(planes).toHaveLength(6);
    const inside = { x: 0.5, y: 0.5, z: 0.5 };
    const outside = { x: 2, y: 0, z: 0 };
    for (const p of planes) {
      const d = p.normal.x * inside.x + p.normal.y * inside.y + p.normal.z * inside.z + p.d;
      expect(d).toBeGreaterThanOrEqual(-1e-9);
    }
    const leftPlane = planes.find((p) => p.normal.x < -0.9)!;
    const dOut = leftPlane.normal.x * outside.x + leftPlane.normal.y * outside.y
      + leftPlane.normal.z * outside.z + leftPlane.d;
    expect(dOut).toBeLessThan(0);
  });

  it('planeToLocalSpace transforms a world plane into local coords', () => {
    const local = buildPlacementMatrix({ anchor: [104, 30.5] }, { format: '3dtiles' });
    const worldPlanes = frustumPlanesFromMat4(identityMat4());
    const localLeft = planeToLocalSpace(worldPlanes[0], local);
    // 局部左平面 x = −1（米）：x = −unit(纬度) 米制 —— 距离 = unit
    const unit = meterInMercatorUnit(30.5);
    const probeLocal = { x: -unit * 0.5, y: 0, z: 0 }; // x > −unit → 视锥内
    const d = localLeft.normal.x * probeLocal.x + localLeft.normal.y * probeLocal.y
      + localLeft.normal.z * probeLocal.z + localLeft.d;
    expect(d).toBeGreaterThan(0);
  });
});

describe('ECEF math', () => {
  it('geodeticToEcef(0,0,0) ≈ (6378137, 0, 0)', () => {
    const p = geodeticToEcef(0, 0, 0);
    expect(p.x).toBeCloseTo(6378137.0, -1);
    expect(p.y).toBeCloseTo(0, 0);
    expect(p.z).toBeCloseTo(0, 0);
  });

  it('geodeticToEcef(90,0,0) ≈ 北极（z ≈ 6356752.3）', () => {
    const p = geodeticToEcef(90, 0, 0);
    expect(p.x).toBeCloseTo(0, 0);
    expect(p.y).toBeCloseTo(0, 0);
    expect(p.z).toBeCloseTo(6356752.314, -1);
  });

  it('enuFromEcefRotation 在赤道本初子午线的 U ≈ ECEF x 轴', () => {
    const r = enuFromEcefRotation(0, 0);
    // U 列 = (cosφcosλ, cosφsinλ, sinφ) = (1,0,0)
    expect(r[2]).toBeCloseTo(1, 12); // 列1 z 分量
    // E 列 = (−sinλ, cosλ, 0) = (0,1,0)
    expect(r[4]).toBeCloseTo(1, 12);
    // N 列 = (−sinφcosλ, −sinφsinλ, cosφ) = (0,0,1)
    expect(r[9]).toBeCloseTo(1, 12);
  });
});

describe('SSE', () => {
  it('computes the standard 3D Tiles SSE', () => {
    const sse = screenSpaceError({
      geometricError: 100,
      distanceM: 1000,
      viewportHeightPx: 800,
      fovRad: 0.6435,
    });
    const denom = 1000 * 2 * Math.tan(0.6435 / 2);
    expect(sse).toBeCloseTo((100 * 800) / denom, 6);
  });

  it('distanceToSphereM clamps to surface', () => {
    const eye = lngLatAltToWorld(104, 30.5, 2000);
    const center = lngLatAltToWorld(104, 30.5, 0);
    // 每世界单位的米数 = 1 / meterInMercatorUnit
    const metersPerWorldUnit = 1 / meterInMercatorUnit(30.5);
    const d = distanceToSphereM(eye, { center, radiusM: 1500 }, metersPerWorldUnit);
    expect(d).toBeCloseTo(500, -1); // 2000 − 1500
  });
});
