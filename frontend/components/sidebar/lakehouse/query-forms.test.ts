/**
 * Lakehouse 查询表单的前端 schema 预校验（评审 P2）。
 *
 * 关注 bbox：scan 路径后端只查四元长度，不查每轴 min<max —— 反向 bbox 会把
 * 全部 row-group 剪掉，静默返回 0 要素。前端必须与 labeled 路径同口径拒绝。
 */
import { describe, it, expect } from 'vitest';
import { parseBbox, buildRequest, EMPTY_FORM } from './query-forms';

describe('parseBbox（min<max 每轴校验）', () => {
  it('合法 bbox 原样返回', () => {
    expect(parseBbox('116.0, 39.0, 116.5, 40.0')).toEqual([116, 39, 116.5, 40]);
  });

  it('四元但缺有限数字 → null', () => {
    expect(parseBbox('116.0, 39.0, 116.5')).toBeNull();
    expect(parseBbox('116.0, 39.0, 116.5, abc')).toBeNull();
  });

  it('反向 / 退化 bbox → null（不再放行到后端静默剪空）', () => {
    expect(parseBbox('10,10,0,0')).toBeNull();
    expect(parseBbox('116.5, 39.0, 116.0, 40.0')).toBeNull();
    expect(parseBbox('116.0, 40.0, 116.5, 39.0')).toBeNull();
    // 单点（min == max）同样退化
    expect(parseBbox('116.0, 39.0, 116.0, 40.0')).toBeNull();
  });
});

describe('buildRequest：scan 反向 bbox 本地拒绝', () => {
  it('scan 模式反向 bbox → 本地错误，不产生请求体', () => {
    const res = buildRequest(
      { ...EMPTY_FORM, mode: 'scan', scan: { ref: 'ref:fabric-parquet/x', bbox: '10,10,0,0', columns: '', maxRows: 1000 } },
      'sess-1',
    );
    expect(res.request).toBeUndefined();
    expect(res.error).toBeTruthy();
  });

  it('scan 模式合法 bbox → 请求体带 bbox', () => {
    const res = buildRequest(
      { ...EMPTY_FORM, mode: 'scan', scan: { ref: 'ref:fabric-parquet/x', bbox: '116.0,39.0,116.5,40.0', columns: '', maxRows: 1000 } },
      'sess-1',
    );
    expect(res.error).toBeUndefined();
    expect(res.request?.payload.bbox).toEqual([116, 39, 116.5, 40]);
  });

  it('labeled 模式反向 bbox 同样本地拒绝（与后端 400 同口径）', () => {
    const res = buildRequest(
      { ...EMPTY_FORM, mode: 'labeled', ref: 'ref:cube/x', labeled: { ...EMPTY_FORM.labeled, bbox: '10,10,0,0' } },
      'sess-1',
    );
    expect(res.request).toBeUndefined();
    expect(res.error).toBeTruthy();
  });
});

describe('buildRequest：window 切片 start ≤ stop 既有语义不回归', () => {
  it('start > stop → 本地错误', () => {
    const res = buildRequest(
      { ...EMPTY_FORM, mode: 'window', ref: 'ref:cube/x', window: { time: '8, 0', y: '', x: '' } },
      'sess-1',
    );
    expect(res.request).toBeUndefined();
    expect(res.error).toBeTruthy();
  });

  it('合法切片 → 请求体带切片', () => {
    const res = buildRequest(
      { ...EMPTY_FORM, mode: 'window', ref: 'ref:cube/x', window: { time: '0, 8', y: '', x: '' } },
      'sess-1',
    );
    expect(res.error).toBeUndefined();
    expect(res.request?.payload.time).toEqual([0, 8]);
  });
});
