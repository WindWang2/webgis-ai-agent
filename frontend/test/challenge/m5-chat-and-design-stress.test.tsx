import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import React, { useState } from 'react';
import { tokenizeCode, getLanguageLabel, getTokenClassName } from '../../components/code-highlight/tokenizer';
import { CodeBlock, parseMessageContent } from '../../components/code-highlight/code-block';
import MiniMd, { safeUrlTransform } from '../../components/chat/mini-md';
import { CollapsibleThink } from '../../components/chat/collapsible-think';

/* eslint-disable @typescript-eslint/no-require-imports */
vi.mock('framer-motion', () => {
  const fm = require('../__mocks__/framer-motion');
  return {
    ...fm,
    AnimatePresence: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  };
});
/* eslint-enable @typescript-eslint/no-require-imports */

// Mock authenticated download & transport for MiniMd
vi.mock('@/lib/api/authenticated-download', () => ({
  isProtectedDownloadUrl: (url: string) =>
    typeof url === 'string' && url.includes('/api/v1/export/download/'),
  downloadWithAuth: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('@/lib/api/first-party', () => ({
  isFirstPartyUrl: (url: string) => typeof url === 'string' && !url.startsWith('https://'),
  toApiPath: (url: string) => url,
  isProtectedDownloadUrl: (url: string) =>
    typeof url === 'string' && url.includes('/api/v1/export/download/'),
}));

vi.mock('@/lib/api/transport', () => ({
  apiFetchBlob: vi.fn().mockResolvedValue({ blob: new Blob(['img'], { type: 'image/png' }), filename: null }),
  describeApiError: (err: unknown, fallback: string) => fallback,
}));

describe('Empirical Challenge Suite: Syntax Tokenizer (tokenizer.ts)', () => {
  describe('Edge Cases & Malformed Inputs', () => {
    it('handles empty code string and empty lines cleanly', () => {
      const resultEmpty = tokenizeCode('', 'python');
      expect(resultEmpty).toHaveLength(1);
      expect(resultEmpty[0]).toEqual([{ type: 'plain', value: '' }]);

      const resultNewlines = tokenizeCode('\n\n\n', 'javascript');
      expect(resultNewlines).toHaveLength(4);
      resultNewlines.forEach((line) => {
        expect(line).toEqual([{ type: 'plain', value: '' }]);
      });
    });

    it('handles unclosed strings without infinite looping or crashing', () => {
      const unclosedSingle = tokenizeCode("let str = 'unclosed string", 'javascript');
      expect(unclosedSingle[0].length).toBeGreaterThan(0);
      const strToken = unclosedSingle[0].find((t) => t.type === 'string');
      expect(strToken?.value).toBe("'unclosed string");

      const unclosedDouble = tokenizeCode('const s = "unterminated double quote', 'typescript');
      expect(unclosedDouble[0].some((t) => t.type === 'string' && t.value === '"unterminated double quote')).toBe(true);

      const unclosedTemplate = tokenizeCode('const s = `unterminated backtick', 'js');
      expect(unclosedTemplate[0].some((t) => t.type === 'string' && t.value === '`unterminated backtick')).toBe(true);
    });

    it('handles strings ending with escape character correctly', () => {
      const escapedEnd = tokenizeCode('const s = "test\\', 'typescript');
      expect(escapedEnd[0].some((t) => t.type === 'string' && t.value === '"test\\')).toBe(true);

      const escapedQuoteInside = tokenizeCode('const s = "test\\"escaped"', 'js');
      expect(escapedQuoteInside[0].some((t) => t.type === 'string' && t.value === '"test\\"escaped"')).toBe(true);
    });

    it('handles empty language by returning a single plain token', () => {
      const resultEmptyLang = tokenizeCode('const x = 1;', '');
      expect(resultEmptyLang[0]).toEqual([{ type: 'plain', value: 'const x = 1;' }]);
    });

    it('handles unknown language with graceful punctuation decomposition', () => {
      const resultUnknown = tokenizeCode('SELECT * FROM table;', 'unknown-custom-dsl');
      // Unknown language processes words as plain and punctuation as punctuation
      expect(resultUnknown[0].length).toBeGreaterThan(1);
      expect(resultUnknown[0].some((t) => t.type === 'punctuation' && t.value === '*')).toBe(true);
    });
  });

  describe('SQL & PostGIS Dialects', () => {
    it('tokenizes standard SQL keywords case-insensitively', () => {
      const sqlUpper = tokenizeCode('SELECT id, name FROM users WHERE active = TRUE;', 'sql');
      const tokensUpper = sqlUpper[0];
      expect(tokensUpper.find((t) => t.value === 'SELECT')?.type).toBe('keyword');
      expect(tokensUpper.find((t) => t.value === 'FROM')?.type).toBe('keyword');
      expect(tokensUpper.find((t) => t.value === 'WHERE')?.type).toBe('keyword');

      const sqlLower = tokenizeCode('select id, name from users where active = true;', 'sql');
      const tokensLower = sqlLower[0];
      expect(tokensLower.find((t) => t.value === 'select')?.type).toBe('keyword');
      expect(tokensLower.find((t) => t.value === 'from')?.type).toBe('keyword');
      expect(tokensLower.find((t) => t.value === 'where')?.type).toBe('keyword');
    });

    it('tokenizes PostGIS spatial function calls and spatial queries', () => {
      const postgis = tokenizeCode('SELECT ST_Buffer(ST_Centroid(geom), 100) AS buf FROM spatial_table;', 'sql');
      const tokens = postgis[0];
      const stBuffer = tokens.find((t) => t.value === 'ST_Buffer');
      expect(stBuffer).toBeDefined();
      expect(stBuffer?.type === 'keyword' || stBuffer?.type === 'function').toBe(true);
    });

    it('tokenizes SQL single-line comments properly', () => {
      const sqlComment = tokenizeCode('SELECT 1; -- this is a comment\n-- whole line comment', 'sql');
      expect(sqlComment[0].find((t) => t.type === 'comment')?.value).toBe('-- this is a comment');
      expect(sqlComment[1][0].type).toBe('comment');
      expect(sqlComment[1][0].value).toBe('-- whole line comment');
    });
  });

  describe('JSON & GeoJSON Property vs Value Recognition', () => {
    it('recognizes property keys vs string values in JSON/GeoJSON', () => {
      const geojson = tokenizeCode('{\n  "type": "Feature",\n  "properties": {\n    "name": "Central Park"\n  }\n}', 'geojson');
      const line1 = geojson[1];
      const typeProp = line1.find((t) => t.type === 'property');
      expect(typeProp?.value).toBe('"type"');
      const featureStr = line1.find((t) => t.type === 'string');
      expect(featureStr?.value).toBe('"Feature"');
    });
  });

  describe('HTML, XML & SVG Tag Parsing', () => {
    it('recognizes HTML/XML/SVG tags properly', () => {
      const html = tokenizeCode('<div className="box">\n  <svg viewBox="0 0 100 100">\n    <path d="M10 10" />\n  </svg>\n</div>', 'html');
      expect(html[0].find((t) => t.type === 'tag')?.value).toBe('<div');
      expect(html[1].find((t) => t.type === 'tag')?.value).toBe('<svg');
      expect(html[2].find((t) => t.type === 'tag')?.value).toBe('<path');
      expect(html[3].find((t) => t.type === 'tag')?.value).toBe('</svg');
      expect(html[4].find((t) => t.type === 'tag')?.value).toBe('</div');
    });

    it('handles solitary < or comparison operator in HTML without crashing', () => {
      const htmlWithLessThan = tokenizeCode('if (a < b && b > c) { return "<div>"; }', 'html');
      expect(htmlWithLessThan[0].length).toBeGreaterThan(0);
    });
  });

  describe('Number Tokenization Character Coverage', () => {
    it('tokenizes integer, negative, decimal, and hex numbers', () => {
      const numCode = tokenizeCode('const a = 123;\nconst b = -456.78;\nconst d = 0xFF;', 'typescript');
      expect(numCode[0].find((t) => t.type === 'number')?.value).toBe('123');
      expect(numCode[1].find((t) => t.type === 'number')?.value).toBe('-456.78');
      expect(numCode[2].find((t) => t.type === 'number')?.value).toBe('0xFF');
    });

    it('documents empirical behavior of scientific notation tokens', () => {
      // In scientific notation (e.g. 1.5e-4), tokenizer emits 1.5e (number) and -4 (number)
      const sciCode = tokenizeCode('const c = 1.5e-4;', 'javascript');
      const numberTokens = sciCode[0].filter((t) => t.type === 'number');
      expect(numberTokens.length).toBeGreaterThanOrEqual(1);
    });
  });

  describe('Fuzz & Stress Inputs', () => {
    it('handles massive 50,000 character single line efficiently (< 500ms)', () => {
      const hugeLine = 'const x = ' + ' "abc" + 123 + '.repeat(3000) + ' null;';
      const start = performance.now();
      const tokens = tokenizeCode(hugeLine, 'javascript');
      const elapsed = performance.now() - start;
      expect(tokens[0].length).toBeGreaterThan(1000);
      expect(elapsed).toBeLessThan(500);
    });

    it('handles Unicode, emoji, and zero-width spaces gracefully', () => {
      const unicodeCode = tokenizeCode('const 🌍 = "🚀 WebGIS"; // 🛰️ 地理信息系统 \u200B', 'javascript');
      expect(unicodeCode[0].length).toBeGreaterThan(0);
      expect(unicodeCode[0].find((t) => t.type === 'string')?.value).toBe('"🚀 WebGIS"');
      expect(unicodeCode[0].find((t) => t.type === 'comment')?.value).toContain('🛰️ 地理信息系统');
    });
  });
});

describe('Empirical Challenge Suite: CodeBlock Component', () => {
  it('renders with custom filename, language pill, and line numbers', () => {
    render(
      <CodeBlock
        language="python"
        filename="geo_transform.py"
        code={`import geopandas as gpd\n\ndef load_data(path):\n    return gpd.read_file(path)\n`}
      />
    );

    expect(screen.getByText('geo_transform.py')).toBeInTheDocument();
    expect(screen.getByTestId('language-pill')).toHaveTextContent('python');
    expect(screen.getByText('1')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
  });

  it('respects showLineNumbers=false override on multi-line code', () => {
    const { container } = render(
      <CodeBlock
        language="python"
        code={`alpha\nbeta\ngamma\ndelta`}
        showLineNumbers={false}
      />
    );

    // Line number container spans with tabular-nums should not be present
    const lineNumberSpans = container.querySelectorAll('span.tabular-nums');
    expect(lineNumberSpans.length).toBe(0);
  });

  it('renders shell commands with terminal icon', () => {
    render(<CodeBlock language="bash" code="pnpm install && pnpm build" />);
    expect(screen.getByTestId('code-block')).toBeInTheDocument();
    expect(screen.getByTestId('language-pill')).toHaveTextContent('bash');
  });

  it('parses message content with mixed text and code fences', () => {
    const parsed = parseMessageContent('Here is some SQL:\n```sql\nSELECT * FROM spatial_ref_sys;\n```\nDone.');
    render(<>{parsed}</>);
    expect(screen.getByText('Here is some SQL:')).toBeInTheDocument();
    expect(screen.getByTestId('code-block')).toBeInTheDocument();
    expect(screen.getByText('Done.')).toBeInTheDocument();
  });
});

describe('Empirical Challenge Suite: MiniMd Streaming & Markdown Stress', () => {
  it('renders deeply nested lists (10 levels deep) without stack overflow or layout break', () => {
    let nestedMd = '- Level 1\n';
    for (let i = 2; i <= 10; i++) {
      nestedMd += '  '.repeat(i - 1) + `- Level ${i}\n`;
    }

    const { container } = render(<MiniMd text={nestedMd} />);
    expect(container.querySelectorAll('ul').length).toBeGreaterThanOrEqual(5);
    expect(screen.getByText('Level 10')).toBeInTheDocument();
  });

  it('renders mixed ordered and unordered lists seamlessly', () => {
    const mixedMd = `1. 第一阶段：数据准备\n   - 导入 Shapefile 数据\n   - 检查坐标参考系 (CRS)\n2. 第二阶段：空间计算\n   1. 执行缓冲区计算\n   2. 执行交集裁剪`;
    render(<MiniMd text={mixedMd} />);

    expect(screen.getByText('第一阶段：数据准备')).toBeInTheDocument();
    expect(screen.getByText('导入 Shapefile 数据')).toBeInTheDocument();
    expect(screen.getByText('第二阶段：空间计算')).toBeInTheDocument();
    expect(screen.getByText('执行缓冲区计算')).toBeInTheDocument();
  });

  it('handles incomplete streaming markdown constructs gracefully', () => {
    // 1. Unclosed code block
    const unclosedCode = 'Here is the code:\n```python\ndef process_raster():\n    pass';
    const { unmount: u1 } = render(<MiniMd text={unclosedCode} />);
    expect(screen.getByText(/process_raster/)).toBeInTheDocument();
    u1();

    // 2. Unclosed table
    const unclosedTable = '| 字段名 | 类型 |\n|---|---|\n| id | integer';
    const { unmount: u2 } = render(<MiniMd text={unclosedTable} />);
    expect(screen.getByText('字段名')).toBeInTheDocument();
    expect(screen.getByText('id')).toBeInTheDocument();
    u2();

    // 3. Unclosed bold / italic
    const unclosedBold = '这是 **未闭合的粗体文本 和 *未闭合的斜体';
    const { unmount: u3 } = render(<MiniMd text={unclosedBold} />);
    expect(screen.getByText(/未闭合的粗体文本/)).toBeInTheDocument();
    u3();
  });

  it('neutralizes malicious XSS attempts in URLs via safeUrlTransform', () => {
    expect(safeUrlTransform('javascript:alert(1)', 'href', null as any)).toBe('');
    expect(safeUrlTransform('data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==', 'href', null as any)).toBe('');
    expect(safeUrlTransform('vbscript:msgbox(1)', 'href', null as any)).toBe('');
    expect(safeUrlTransform('https://example.com/map', 'href', null as any)).toBe('https://example.com/map');
    expect(safeUrlTransform('/api/v1/export/download/report.pdf', 'href', null as any)).toBe('/api/v1/export/download/report.pdf');
    expect(safeUrlTransform('#layer-section', 'href', null as any)).toBe('#layer-section');
  });

  it('simulates rapid multi-chunk streaming updates without throwing or tearing', () => {
    function StreamingWrapper() {
      const [chunks, setChunks] = useState('初始化...');
      return (
        <div>
          <button
            onClick={() => {
              setChunks((prev) => prev + '\n\n```python\n# Step ' + Math.random() + '\nprint("Streaming chunk")\n```');
            }}
            data-testid="append-btn"
          >
            Append
          </button>
          <MiniMd text={chunks} />
        </div>
      );
    }

    render(<StreamingWrapper />);
    const appendBtn = screen.getByTestId('append-btn');

    act(() => {
      for (let i = 0; i < 20; i++) {
        fireEvent.click(appendBtn);
      }
    });

    expect(screen.getAllByTestId('code-block').length).toBe(20);
  });
});

describe('Empirical Challenge Suite: CollapsibleThink Component', () => {
  it('returns null when content is empty and streaming is false', () => {
    const { container } = render(<CollapsibleThink content="" isStreaming={false} isThinking={false} />);
    expect(container.firstChild).toBeNull();
  });

  it('renders generating placeholder when active but content is initially empty', () => {
    render(<CollapsibleThink content="" isStreaming={true} />);
    expect(screen.getByRole('button', { name: /深度思考中/i })).toBeInTheDocument();
    expect(screen.getByText('正在生成思考链...')).toBeInTheDocument();
  });

  it('formats various durations accurately (ms vs s)', () => {
    const { unmount: u1 } = render(<CollapsibleThink content="thought" durationMs={350} />);
    expect(screen.getByText('350ms')).toBeInTheDocument();
    u1();

    const { unmount: u2 } = render(<CollapsibleThink content="thought" durationMs={1200} />);
    expect(screen.getByText('1.2s')).toBeInTheDocument();
    u2();

    const { unmount: u3 } = render(<CollapsibleThink content="thought" durationMs={45678} />);
    expect(screen.getByText('45.7s')).toBeInTheDocument();
    u3();
  });

  it('respects manual user collapse even while streaming continues', async () => {
    const { rerender } = render(
      <CollapsibleThink content="第 1 步思考..." isStreaming={true} />
    );

    const button = screen.getByRole('button', { name: /深度思考中/i });
    expect(button).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('第 1 步思考...')).toBeInTheDocument();

    // User explicitly clicks to collapse
    await act(async () => {
      fireEvent.click(button);
    });

    expect(button).toHaveAttribute('aria-expanded', 'false');

    // Streaming continues with more tokens
    rerender(
      <CollapsibleThink content="第 1 步思考...\n第 2 步思考..." isStreaming={true} />
    );

    // User manual collapse must NOT be overridden by streaming
    expect(button).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText(/第 2 步思考/)).not.toBeInTheDocument();

    // User clicks again to expand
    await act(async () => {
      fireEvent.click(button);
    });

    expect(button).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText(/第 2 步思考/)).toBeInTheDocument();
  });

  it('transitions cleanly from active streaming to finished state with badges', () => {
    const { rerender } = render(
      <CollapsibleThink content="正在思考空间相交算子..." isStreaming={true} />
    );

    expect(screen.getByRole('button', { name: /深度思考中/i })).toBeInTheDocument();
    expect(screen.queryByText(/tokens/)).not.toBeInTheDocument();

    // Stream completes
    rerender(
      <CollapsibleThink
        content="空间相交算子分析完成：使用 ST_Intersects 进行空间连接"
        isStreaming={false}
        durationMs={2400}
        tokenCount={450}
      />
    );

    expect(screen.getByRole('button', { name: /思考过程/i })).toBeInTheDocument();
    expect(screen.getByText('2.4s')).toBeInTheDocument();
    expect(screen.getByText('450 tokens')).toBeInTheDocument();
  });
});

describe('Empirical Challenge Suite: Design System Token Alignment', () => {
  it('uses standard semantic tokens for code highlight classes', () => {
    expect(getTokenClassName('keyword')).toContain('text-purple-600');
    expect(getTokenClassName('keyword')).toContain('dark:text-purple-400');
    expect(getTokenClassName('string')).toContain('text-emerald-600');
    expect(getTokenClassName('string')).toContain('dark:text-emerald-400');
    expect(getTokenClassName('number')).toContain('text-amber-600');
    expect(getTokenClassName('comment')).toContain('text-ink-muted');
    expect(getTokenClassName('property')).toContain('text-sky-600');
    expect(getTokenClassName('plain')).toContain('text-ink');
  });

  it('maps language identifiers to canonical display labels', () => {
    expect(getLanguageLabel('py')).toBe('Python');
    expect(getLanguageLabel('python')).toBe('Python');
    expect(getLanguageLabel('ts')).toBe('TypeScript');
    expect(getLanguageLabel('js')).toBe('JavaScript');
    expect(getLanguageLabel('sql')).toBe('SQL');
    expect(getLanguageLabel('geojson')).toBe('GeoJSON');
    expect(getLanguageLabel('bash')).toBe('Bash');
    expect(getLanguageLabel('sh')).toBe('Shell');
  });
});
