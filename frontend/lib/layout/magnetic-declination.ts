/**
 * magnetic-declination —— 真北/磁北偏角近似（AC-07 / ADR-0156 §P4）。
 *
 * 数据不可得（无 WMM 系数表/无外部服务）时的确定性近似（§0.5 默认决策）：
 * 偶极子模型 —— 磁北方向 = 指向地磁极的初始方位角。用 DGRF2020 地磁极
 * (80.65°N, 72.68°W) 做 spherical-triangle 求解，与 WMM 实测值偏差通常
 * 在数度内 —— **恒标 approximate**，指北针旁以「≈」呈现，不谎报精度。
 *
 * 纯函数；输出归一到 (-180, 180]，东正西负。
 */

/** DGRF2020 地磁北极（倾角极）坐标（度）。 */
const GEOMAGNETIC_POLE = { lat: 80.65, lng: -72.68 };

const D2R = Math.PI / 180;
const R2D = 180 / Math.PI;

/**
 * bbox 中心（或任一点）的磁偏角近似值。
 * @returns degrees ∈ (-180,180]（东正西负）+ approximate 恒 true + 标签。
 */
export function magneticDeclinationAt(
  lat: number,
  lng: number,
): { degrees: number; approximate: boolean; label: string } {
  if (
    !Number.isFinite(lat) || !Number.isFinite(lng)
    || Math.abs(lat) > 90
  ) {
    return { degrees: 0, approximate: true, label: '≈0.0°E (approximate)' };
  }
  const φ = lat * D2R;
  const φp = GEOMAGNETIC_POLE.lat * D2R;
  const Δλ = (lng - GEOMAGNETIC_POLE.lng) * D2R;

  const y = Math.sin(Δλ) * Math.cos(φp);
  const x = Math.cos(φ) * Math.sin(φp)
    - Math.sin(φ) * Math.cos(φp) * Math.cos(Δλ);
  const bearing = Math.atan2(y, x) * R2D; // 指向地磁极的初始方位角 = 磁北方向
  // 归一 (-180, 180]
  let degrees = bearing;
  if (degrees <= -180) degrees += 360;
  if (degrees > 180) degrees -= 360;
  const hemi = degrees >= 0 ? 'E' : 'W';
  return {
    degrees,
    approximate: true,
    label: `≈${Math.abs(degrees).toFixed(1)}°${hemi} (approximate)`,
  };
}

/** bbox 中心点（四角均值；跨经度 180 的退化输入按算术均值处理并披露）。 */
export function bboxCenter(
  bounds: { west: number; south: number; east: number; north: number },
): { lat: number; lng: number } {
  return {
    lat: (bounds.south + bounds.north) / 2,
    lng: (bounds.west + bounds.east) / 2,
  };
}
