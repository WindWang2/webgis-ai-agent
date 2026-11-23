import { describe, expect, it } from 'vitest';
import { styleCommands } from './styleCommands';

/**
 * apply_style_intent 的 requiredParams 必须在派发前拒绝不可消费的数值参数：
 * run 只拷贝 `typeof === 'number'` 的字段，非数字一旦放行会被静默丢弃，
 * applier 按缺省语义落地（lighten 默认 amount）或误报 invalid_style_intent
 * （set_opacity 缺 opacity），而 dispatcher 仍按命令语义 ack。
 */
const validate = (params: Record<string, unknown>): boolean =>
  styleCommands.apply_style_intent.requiredParams(params);

describe('apply_style_intent requiredParams (numeric payload contract)', () => {
  it('rejects non-numeric opacity/width/size instead of silently dropping them', () => {
    expect(validate({ layerId: 'l', kind: 'set_opacity', opacity: '0.5' })).toBe(false);
    expect(validate({ layerId: 'l', kind: 'set_stroke_width', width: '2' })).toBe(false);
    expect(validate({ layerId: 'l', kind: 'set_point_size', size: '4' })).toBe(false);
  });

  it('rejects a non-numeric relative amount instead of applying the default semantics', () => {
    expect(validate({ layerId: 'l', kind: 'lighten', amount: '0.3' })).toBe(false);
    expect(validate({ layerId: 'l', kind: 'thinner', amount: true })).toBe(false);
  });

  it('still accepts the intended numeric payloads and the absent-amount defaults', () => {
    expect(validate({ layerId: 'l', kind: 'set_opacity', opacity: 0.5 })).toBe(true);
    expect(validate({ layerId: 'l', kind: 'set_stroke_width', width: 2 })).toBe(true);
    expect(validate({ layerId: 'l', kind: 'set_point_size', size: 4 })).toBe(true);
    expect(validate({ layerId: 'l', kind: 'lighten' })).toBe(true);
    expect(validate({ layerId: 'l', kind: 'lighten', amount: 0.3 })).toBe(true);
  });
});
