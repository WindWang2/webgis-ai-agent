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
  let out = '';
  let quote = '';
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quote) {
      out += ch;
      if (ch === quote) {
        if (text[i + 1] === quote) { out += text[++i]; } else { quote = ''; }
      } else if (ch === '\\' && i + 1 < text.length) { out += text[++i]; }
    } else if (ch === "'" || ch === '"' || ch === '`') {
      quote = ch;
      out += ch;
    } else if (ch === '#' || (ch === '-' && text[i + 1] === '-')) {
      while (i < text.length && text[i] !== '\n') i++;
      out += i < text.length ? ' \n' : ' ';
    } else if (ch === '/' && text[i + 1] === '*') {
      i += 2;
      while (i < text.length && !(text[i] === '*' && text[i + 1] === '/')) i++;
      i++;
      out += ' ';
    } else {
      out += ch;
    }
  }
  return out;
}

export interface GuardVerdict {
  /** true = 允许发送。 */
  ok: boolean;
  /** 拦截原因（ok=false 时给出可操作的解释）。 */
  reason?: string;
  /** 命中的动词（供 UI 标注）。 */
  verbs: string[];
}

/** Quoted literals are not statements — mask them before verb / semicolon checks. */
function maskQuotedLiterals(text: string): string {
  let out = '';
  let quote = '';
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quote) {
      if (ch === quote) {
        if (text[i + 1] === quote) {
          out += '  ';
          i += 1;
        } else {
          quote = '';
          out += ' ';
        }
      } else if (ch === '\\' && i + 1 < text.length) {
        out += '  ';
        i += 1;
      } else {
        out += ' ';
      }
    } else if (ch === "'" || ch === '"' || ch === '`') {
      quote = ch;
      out += ' ';
    } else {
      out += ch;
    }
  }
  return out;
}

/**
 * 校验 where / filter_expr 文本。
 * 规则：
 *  1) 写类动词 → 拦截（只读端点）；
 *  2) 多语句（顶层 `;` 后仍有实质内容）→ 拦截（单表达式契约）。
 * 引号内的文本先换成占位空白，字面量里的分号和动词不拦截。
 */
export function guardFilterText(text: string): GuardVerdict {
  const bare = maskQuotedLiterals(stripSqlComments(text ?? ''));
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
