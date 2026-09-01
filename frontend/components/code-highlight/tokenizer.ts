/**
 * Lightweight, streaming-resilient syntax tokenizer for WebGIS code blocks.
 * Supports Python, JavaScript, TypeScript, SQL, JSON, Bash, HTML, CSS, GeoJSON, YAML, Markdown.
 */

export interface Token {
  type: 'keyword' | 'string' | 'number' | 'comment' | 'property' | 'function' | 'operator' | 'punctuation' | 'tag' | 'plain';
  value: string;
}

const KEYWORDS_PYTHON = new Set([
  'def', 'class', 'import', 'from', 'return', 'if', 'elif', 'else', 'for', 'while',
  'try', 'except', 'finally', 'with', 'as', 'lambda', 'yield', 'async', 'await',
  'pass', 'break', 'continue', 'raise', 'in', 'is', 'not', 'and', 'or', 'global',
  'nonlocal', 'assert', 'del', 'None', 'True', 'False'
]);

const KEYWORDS_JS_TS = new Set([
  'const', 'let', 'var', 'function', 'return', 'if', 'else', 'for', 'while', 'do',
  'switch', 'case', 'default', 'try', 'catch', 'finally', 'throw', 'new', 'delete',
  'typeof', 'instanceof', 'void', 'this', 'super', 'class', 'extends', 'import',
  'export', 'from', 'as', 'default', 'async', 'await', 'yield', 'interface', 'type',
  'enum', 'namespace', 'module', 'declare', 'abstract', 'implements', 'private',
  'protected', 'public', 'readonly', 'static', 'override', 'keyof',
  'true', 'false', 'null', 'undefined', 'NaN', 'Infinity'
]);

const KEYWORDS_SQL = new Set([
  'SELECT', 'FROM', 'WHERE', 'JOIN', 'LEFT', 'RIGHT', 'INNER', 'OUTER', 'FULL',
  'CROSS', 'ON', 'GROUP', 'BY', 'ORDER', 'HAVING', 'LIMIT', 'OFFSET', 'UNION',
  'ALL', 'INSERT', 'INTO', 'VALUES', 'UPDATE', 'SET', 'DELETE', 'CREATE', 'TABLE',
  'DROP', 'ALTER', 'ADD', 'COLUMN', 'PRIMARY', 'KEY', 'FOREIGN', 'REFERENCES',
  'INDEX', 'VIEW', 'AND', 'OR', 'NOT', 'IN', 'LIKE', 'ILIKE', 'IS', 'NULL',
  'BETWEEN', 'EXISTS', 'CASE', 'WHEN', 'THEN', 'ELSE', 'END', 'AS', 'DISTINCT',
  'COUNT', 'SUM', 'AVG', 'MIN', 'MAX', 'CAST', 'COALESCE', 'WITH', 'RECURSIVE',
  'ST_Buffer', 'ST_Within', 'ST_Contains', 'ST_Intersects', 'ST_Distance',
  'ST_Area', 'ST_Centroid', 'ST_Union', 'ST_Intersection', 'ST_Transform',
  'ST_SetSRID', 'ST_GeomFromText', 'ST_GeomFromGeoJSON', 'ST_AsGeoJSON'
]);

const KEYWORDS_BASH = new Set([
  'if', 'then', 'else', 'elif', 'fi', 'for', 'in', 'do', 'done', 'while', 'until',
  'case', 'esac', 'function', 'return', 'exit', 'export', 'source', 'alias',
  'echo', 'cd', 'ls', 'pwd', 'mkdir', 'rm', 'cp', 'mv', 'cat', 'grep', 'sed',
  'awk', 'curl', 'wget', 'git', 'pnpm', 'npm', 'yarn', 'npx', 'node', 'python',
  'pip', 'docker', 'kubectl', 'sudo', 'chmod', 'chown', 'tar', 'gzip', 'find'
]);

export function tokenizeCode(code: string, language: string = ''): Token[][] {
  const lang = (language || '').toLowerCase().trim();
  const lines = code.split('\n');

  return lines.map((line) => {
    const rawTokens = tokenizeLine(line, lang);
    return mergePlainTokens(rawTokens);
  });
}

function mergePlainTokens(tokens: Token[]): Token[] {
  if (tokens.length <= 1) return tokens;
  const merged: Token[] = [];

  for (const tok of tokens) {
    const last = merged[merged.length - 1];
    if (last && last.type === 'plain' && tok.type === 'plain') {
      last.value += tok.value;
    } else {
      merged.push({ ...tok });
    }
  }

  return merged;
}

function tokenizeLine(line: string, lang: string): Token[] {
  if (!line) return [{ type: 'plain', value: '' }];
  // If no language specified or unknown, return plain text
  if (!lang) return [{ type: 'plain', value: line }];

  const tokens: Token[] = [];
  let i = 0;
  const len = line.length;

  const isSql = lang === 'sql';
  const isPython = lang === 'python' || lang === 'py';
  const isBash = lang === 'bash' || lang === 'shell' || lang === 'sh' || lang === 'zsh';
  const isJson = lang === 'json' || lang === 'geojson';
  const isJsTs = lang === 'javascript' || lang === 'js' || lang === 'typescript' || lang === 'ts' || lang === 'jsx' || lang === 'tsx';
  const isHtml = lang === 'html' || lang === 'xml' || lang === 'svg';

  while (i < len) {
    // 1. Comments
    if (isPython || isBash) {
      if (line[i] === '#') {
        tokens.push({ type: 'comment', value: line.slice(i) });
        break;
      }
    } else if (isSql) {
      if (line.slice(i, i + 2) === '--') {
        tokens.push({ type: 'comment', value: line.slice(i) });
        break;
      }
    } else {
      if (line.slice(i, i + 2) === '//') {
        tokens.push({ type: 'comment', value: line.slice(i) });
        break;
      }
    }

    // 2. Strings: ", ', or `
    if (line[i] === '"' || line[i] === "'" || line[i] === '`') {
      const quote = line[i];
      let j = i + 1;
      let escaped = false;
      while (j < len) {
        if (line[j] === '\\' && !escaped) {
          escaped = true;
          j++;
          continue;
        }
        if (line[j] === quote && !escaped) {
          j++;
          break;
        }
        escaped = false;
        j++;
      }
      const strVal = line.slice(i, j);

      // In JSON, string followed by ':' is a property/key
      if (isJson) {
        const restAfter = line.slice(j).trimStart();
        if (restAfter.startsWith(':')) {
          tokens.push({ type: 'property', value: strVal });
        } else {
          tokens.push({ type: 'string', value: strVal });
        }
      } else {
        tokens.push({ type: 'string', value: strVal });
      }
      i = j;
      continue;
    }

    // 3. HTML / XML Tags
    if (isHtml && line[i] === '<') {
      let j = i + 1;
      if (line[j] === '/') j++;
      while (j < len && /[a-zA-Z0-9_-]/.test(line[j])) j++;
      if (j > i + 1) {
        tokens.push({ type: 'tag', value: line.slice(i, j) });
        i = j;
        continue;
      }
    }

    // 4. Numbers
    if (/[0-9]/.test(line[i]) || (line[i] === '-' && i + 1 < len && /[0-9]/.test(line[i + 1]))) {
      let j = i;
      if (line[j] === '-') j++;
      while (j < len && /[0-9.eExXa-fA-F_]/.test(line[j])) j++;
      tokens.push({ type: 'number', value: line.slice(i, j) });
      i = j;
      continue;
    }

    // 5. Identifiers / Keywords / Function calls
    if (/[a-zA-Z_$]/.test(line[i])) {
      let j = i;
      while (j < len && /[a-zA-Z0-9_$]/.test(line[j])) j++;
      const word = line.slice(i, j);

      // Check keyword
      let isKw = false;
      if (isPython && KEYWORDS_PYTHON.has(word)) isKw = true;
      else if (isJsTs && KEYWORDS_JS_TS.has(word)) isKw = true;
      else if (isSql && (KEYWORDS_SQL.has(word.toUpperCase()) || KEYWORDS_SQL.has(word))) isKw = true;
      else if (isBash && KEYWORDS_BASH.has(word)) isKw = true;

      if (isKw) {
        tokens.push({ type: 'keyword', value: word });
      } else {
        // Check if followed by '(' -> function call
        const rest = line.slice(j).trimStart();
        if (rest.startsWith('(')) {
          tokens.push({ type: 'function', value: word });
        } else {
          tokens.push({ type: 'plain', value: word });
        }
      }
      i = j;
      continue;
    }

    // 6. Operators and Punctuation
    if (/[{}()[\].,;:?!=<>+\-*/%&|^~]/.test(line[i])) {
      tokens.push({ type: 'punctuation', value: line[i] });
      i++;
      continue;
    }

    // 7. Whitespace or other characters
    let j = i;
    while (j < len && /\s/.test(line[j])) j++;
    if (j > i) {
      tokens.push({ type: 'plain', value: line.slice(i, j) });
      i = j;
      continue;
    }

    // Fallback single character
    tokens.push({ type: 'plain', value: line[i] });
    i++;
  }

  return tokens;
}

export const LANGUAGE_LABELS: Record<string, string> = {
  python: 'Python',
  py: 'Python',
  javascript: 'JavaScript',
  js: 'JavaScript',
  typescript: 'TypeScript',
  ts: 'TypeScript',
  jsx: 'JSX',
  tsx: 'TSX',
  json: 'JSON',
  geojson: 'GeoJSON',
  sql: 'SQL',
  bash: 'Bash',
  shell: 'Shell',
  sh: 'Shell',
  zsh: 'Zsh',
  html: 'HTML',
  xml: 'XML',
  svg: 'SVG',
  css: 'CSS',
  scss: 'SCSS',
  yaml: 'YAML',
  yml: 'YAML',
  markdown: 'Markdown',
  md: 'Markdown',
};

export function getLanguageLabel(lang?: string): string {
  if (!lang) return '';
  const key = lang.toLowerCase().trim();
  return LANGUAGE_LABELS[key] || lang;
}

export function getTokenClassName(type: Token['type']): string {
  switch (type) {
    case 'keyword':
      return 'text-purple-600 dark:text-purple-400 font-medium';
    case 'string':
      return 'text-emerald-600 dark:text-emerald-400';
    case 'number':
      return 'text-amber-600 dark:text-amber-400';
    case 'comment':
      return 'text-ink-muted italic';
    case 'property':
      return 'text-sky-600 dark:text-sky-400 font-medium';
    case 'function':
      return 'text-blue-600 dark:text-blue-400';
    case 'tag':
      return 'text-rose-600 dark:text-rose-400 font-medium';
    case 'punctuation':
    case 'operator':
      return 'text-ink-secondary';
    case 'plain':
    default:
      return 'text-ink';
  }
}
