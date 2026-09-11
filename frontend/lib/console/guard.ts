'use client';

/**
 * 查询控制台危险语句守卫（ADR-0147）。
 *
 * 契约依据（P0 勘察 §3.1）：`POST /data-fabric/catalog/{id}/query` 是只读
 * 检索端点——QuerySpec 无任何写语义（filter/where/columns/limit/...）。因此
 * 写类语句必然失败或被数据源本地拒绝；前端在发送前拦截并诚实披露，比让
 * 用户撞一个 4xx/502 更可理解。这不是沙箱，只是诚实的前置校验。
 */

/** 明确的写/DDL/管理类动词（词边界匹配，大小写不敏感）。 */
const BLOCKED_VERBS = [
  'insert',
  'update',
  'delete',
  'drop',
  'alter',
  'create',
  'truncate',
  'grant',
  'revoke',
  'exec',
  'execute',
  'merge',
  'call',
  'vacuum',
  'analyze', // 某些方言里 ANALYZE 触发维护操作
] as const;

/** 剥掉 `--`/`/* *\/`/`#` 注释，防止注释伪装绕过关键词检测。 */
export function stripSqlComments(text: string): string {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/--[^\n]*/g, ' ')
    .replace(/#[^\n]*/g, ' ');
}

export interface GuardVerdict {
  /** true = 允许发送。 */
  ok: boolean;
  /** 拦截原因（ok=false 时给出可操作的解释）。 */
  reason?: string;
  /** 命中的动词（供 UI 标注）。 */
  verbs: string[];
}

/**
 * 校验 where / filter_expr 文本。
 * 规则：
 *  1) 写类动词 → 拦截（只读端点）；
 *  2) 多语句（顶层 `;` 后仍有实质内容）→ 拦截（单表达式契约）。
 */
export function guardFilterText(text: string): GuardVerdict {
  const bare = stripSqlComments(text ?? '');
  const verbs: string[] = [];
  for (const verb of BLOCKED_VERBS) {
    const re = new RegExp(`(^|[^\\w"'])(${verb})(?=[\\s(']|$)`, 'i');
    if (re.test(bare)) verbs.push(verb);
  }
  if (verbs.length > 0) {
    return {
      ok: false,
      reason: `查询端点为只读检索，不支持写操作：检测到 ${[...new Set(verbs)].map((v) => v.toUpperCase()).join(' / ')}。`,
      verbs,
    };
  }
  const withoutTrailingSemicolon = bare.replace(/;\s*$/, '');
  if (/;\s*\S/.test(withoutTrailingSemicolon)) {
    return {
      ok: false,
      reason: '检测到多语句（顶层分号后仍有内容）。此处接受单个过滤表达式，请拆分后执行。',
      verbs: [],
    };
  }
  return { ok: true, verbs: [] };
}
