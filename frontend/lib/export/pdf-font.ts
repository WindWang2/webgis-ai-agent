/**
 * ac-08（ADR-0157 P3）· PDF 出版字体：CJK 子集嵌入。
 *
 * 仓内 vendored OFL 字体（frontend/public/fonts/NotoSansSC-Regular-subset.ttf，
 * 与 app/lib/cartography/fonts 同一份构建产物）：ASCII + GB2312 全表（6763
 * 汉字）+ 常用标点，2.3MB TrueType/glyf（jsPDF 仅支持 glyf 轮廓，不支持 CFF）。
 *
 * 语义（§0.5 契约）：
 * - 字体加载 + 注册成功 → PDF 文本层可承载中文（可选取/可检索/可复制），
 *   `pdf_text_rasterized_cjk` 不再是默认路径；
 * - 加载/注册任何失败 → 调用方回退画布栅格化（最后兜底，诊断照发）。
 * 模块级缓存：成功载荷只取一次；失败不缓存（瞬时失败不禁用整场 CJK 文本层）。
 */

export const PUBLICATION_FONT_FAMILY = 'NotoSansSC';
export const PUBLICATION_FONT_FILE = 'NotoSansSC-Regular-subset.ttf';
const PUBLICATION_FONT_URL = `/fonts/${PUBLICATION_FONT_FILE}`;

let cachedB64: string | null | undefined;

/** ArrayBuffer → base64（0x8000 分块，避免 apply 参数上限）。 */
function arrayBufferToBase64(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let binary = '';
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

/**
 * 加载出版字体为 base64（jsPDF VFS 形态）。失败 → null（含缺 fetch/网络/
 * 非 TTF 载荷），调用方走兜底。模块级缓存**只缓存成功**（review：一次瞬时
 * 取字失败若把 null 钉死整个会话，CJK 文本层会被永久禁用）；失败的导出重试
 * 代价可接受（字体请求是本地静态资源，失败路径快速返回）。
 */
export async function loadPublicationFontB64(): Promise<string | null> {
  if (cachedB64 !== undefined) return cachedB64;
  const result = await loadUncached();
  if (result !== null) cachedB64 = result;
  return result;
}

async function loadUncached(): Promise<string | null> {
  try {
    if (typeof fetch !== 'function') return null;
    const res = await fetch(PUBLICATION_FONT_URL);
    if (!res.ok) return null;
    const buf = await res.arrayBuffer();
    // TTF 魔数校验（0x00010000）—— 防止把 404 HTML 当字体塞进 VFS。
    if (buf.byteLength < 12) return null;
    const magic = new DataView(buf).getUint32(0);
    if (magic !== 0x00010000 && magic !== 0x74727565 /* 'true' */) return null;
    return arrayBufferToBase64(buf);
  } catch {
    return null;
  }
}

/** 测试/预加载注入口（生产代码勿用）。undefined = 清除缓存（恢复未加载态）。 */
export function __setPublicationFontCacheForTests(b64: string | null | undefined): void {
  cachedB64 = b64;
}

/**
 * 向 jsPDF 实例注册出版字体并设为当前字体。任何一步失败（实例缺 VFS API、
 * 字体损坏）→ false，调用方维持标准 14 字体 + 栅格化兜底。
 */
export function ensurePublicationFont(
  doc: {
    addFileToVFS?: (name: string, data: string) => void;
    addFont?: (file: string, family: string, style: string) => void;
    setFont: (family: string, style?: string) => void;
  },
  fontB64: string | null,
): boolean {
  if (!fontB64) return false;
  // VFS API 不全 → 直接判失败（注册一半的字体比标准字体更糟）。
  if (typeof doc.addFileToVFS !== 'function' || typeof doc.addFont !== 'function') {
    return false;
  }
  try {
    doc.addFileToVFS(PUBLICATION_FONT_FILE, fontB64);
    doc.addFont(PUBLICATION_FONT_FILE, PUBLICATION_FONT_FAMILY, 'normal');
    doc.setFont(PUBLICATION_FONT_FAMILY, 'normal');
    return true;
  } catch {
    return false;
  }
}
