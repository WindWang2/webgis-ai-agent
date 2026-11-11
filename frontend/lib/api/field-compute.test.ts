/** 字段计算错误信封解析回归（G19）：legacy detail 与仓库统一 data 信封都要保真。 */
import { describe, expect, it } from 'vitest';

import { parseFieldComputeError } from './field-compute';

const STRUCTURED = {
  code: 'UNKNOWN_FUNCTION',
  detail: '未注册函数：open',
  position: 9,
  hint: '函数词表见 field_compute_catalog；禁止 import/文件/任意调用',
};

describe('parseFieldComputeError', () => {
  it('keeps code/position/hint from legacy {"detail": {...}}', () => {
    expect(parseFieldComputeError({ body: { detail: STRUCTURED } })).toEqual({
      code: 'UNKNOWN_FUNCTION',
      detail: '未注册函数：open',
      position: 9,
      hint: '函数词表见 field_compute_catalog；禁止 import/文件/任意调用',
    });
  });

  it('keeps code/position/hint from unified error envelope {"data": {...}}', () => {
    // app/core/exception.py::unified_http_exception_handler 把结构化 detail
    // 移入 data；只认 detail 会让生产路径的编译期错误码与修复建议消失。
    expect(parseFieldComputeError({
      body: { message: 'Unprocessable Entity', data: STRUCTURED },
    })).toEqual({
      code: 'UNKNOWN_FUNCTION',
      detail: '未注册函数：open',
      position: 9,
      hint: '函数词表见 field_compute_catalog；禁止 import/文件/任意调用',
    });
  });

  it('handles a plain string detail and a bare structured body', () => {
    expect(parseFieldComputeError({ body: { detail: 'boom' } })).toEqual({ detail: 'boom' });
    expect(parseFieldComputeError({ body: STRUCTURED })).toEqual({
      code: 'UNKNOWN_FUNCTION',
      detail: '未注册函数：open',
      position: 9,
      hint: '函数词表见 field_compute_catalog；禁止 import/文件/任意调用',
    });
  });
});
