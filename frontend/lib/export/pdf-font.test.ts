import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  PUBLICATION_FONT_FAMILY,
  PUBLICATION_FONT_FILE,
  __setPublicationFontCacheForTests,
  ensurePublicationFont,
  loadPublicationFontB64,
} from './pdf-font';

// 最小 TTF 头（0x00010000 magic）——魔数校验通过的最小载荷。
const TTF_LIKE_B64 = (() => {
  const bytes = new Uint8Array(12);
  const dv = new DataView(bytes.buffer);
  dv.setUint32(0, 0x00010000);
  let s = '';
  bytes.forEach((b) => {
    s += String.fromCharCode(b);
  });
  return btoa(s);
})();

afterEach(() => {
  __setPublicationFontCacheForTests(undefined);
  vi.unstubAllGlobals();
});

describe('loadPublicationFontB64（ADR-0157 P3）', () => {
  it('fetch 成功且 TTF 魔数合法 → base64，并模块级缓存', async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve({
        ok: true,
        arrayBuffer: async () => Uint8Array.from(atob(TTF_LIKE_B64), (c) => c.charCodeAt(0)).buffer,
      } as unknown as Response),
    );
    vi.stubGlobal('fetch', fetchMock);
    const b64 = await loadPublicationFontB64();
    expect(b64).toBe(TTF_LIKE_B64);
    // 缓存：第二次不发起 fetch
    const again = await loadPublicationFontB64();
    expect(again).toBe(TTF_LIKE_B64);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toContain('/fonts/');
  });

  it('非 TTF 载荷（如 404 HTML）→ null（拒绝把垃圾塞进 VFS）', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          arrayBuffer: async () => new TextEncoder().encode('<html>404</html>').buffer,
        } as unknown as Response),
      ),
    );
    expect(await loadPublicationFontB64()).toBeNull();
  });

  it('HTTP 失败 → null', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve({ ok: false } as unknown as Response)),
    );
    expect(await loadPublicationFontB64()).toBeNull();
  });

  it('review: 瞬时失败不被缓存 —— 下次调用重试，成功后该结果才入缓存', async () => {
    // 第一次：网络瞬时失败；第二次：成功。
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: false } as unknown as Response)
      .mockResolvedValueOnce({
        ok: true,
        arrayBuffer: async () =>
          Uint8Array.from(atob(TTF_LIKE_B64), (c) => c.charCodeAt(0)).buffer,
      } as unknown as Response);
    vi.stubGlobal('fetch', fetchMock);

    expect(await loadPublicationFontB64()).toBeNull();
    expect(await loadPublicationFontB64()).toBe(TTF_LIKE_B64);
    expect(fetchMock).toHaveBeenCalledTimes(2);

    // 成功入缓存后：第三次不再发起 fetch（计数不增长）。
    expect(await loadPublicationFontB64()).toBe(TTF_LIKE_B64);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe('ensurePublicationFont', () => {
  const fontB64 = TTF_LIKE_B64;

  it('注册成功 → addFileToVFS/addFont/setFont(NotoSansSC) 依序调用，返回 true', () => {
    const calls: string[] = [];
    const doc = {
      addFileToVFS: vi.fn(() => calls.push('vfs')),
      addFont: vi.fn(() => calls.push('font')),
      setFont: vi.fn((family: string) => calls.push(`set:${family}`)),
    };
    expect(ensurePublicationFont(doc, fontB64)).toBe(true);
    expect(doc.addFileToVFS).toHaveBeenCalledWith(PUBLICATION_FONT_FILE, fontB64);
    expect(doc.addFont).toHaveBeenCalledWith(PUBLICATION_FONT_FILE, PUBLICATION_FONT_FAMILY, 'normal');
    expect(calls).toEqual(['vfs', 'font', `set:${PUBLICATION_FONT_FAMILY}`]);
  });

  it('实例缺 VFS API → false（调用方回退标准字体 + 栅格化兜底）', () => {
    const doc = { setFont: vi.fn() };
    expect(ensurePublicationFont(doc, fontB64)).toBe(false);
    expect(doc.setFont).not.toHaveBeenCalled();
  });

  it('fontB64 缺席 → false 且不触碰实例', () => {
    const doc = {
      addFileToVFS: vi.fn(),
      addFont: vi.fn(),
      setFont: vi.fn(),
    };
    expect(ensurePublicationFont(doc, null)).toBe(false);
    expect(doc.addFileToVFS).not.toHaveBeenCalled();
  });
});
