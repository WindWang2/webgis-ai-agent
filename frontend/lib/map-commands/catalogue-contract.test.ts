import { describe, expect, it } from 'vitest';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, extname } from 'node:path';
import { COMMAND_CATALOGUE } from './catalogue';

/**
 * #535 契约不变量：后端发射的每个 command 值都必须存在于前端命令目录。
 *
 * #205-#208 命令迁移时 query_map_features 继续发射 {command: 'query_features'}，
 * 但前端目录从未登记该命令 → 每次点探查都是后端成功 + 前端 unknown_command
 * 失败（prompt.py 还主动推荐它，所以是稳定的一等失败路径）。本测试扫描
 * app/ 下所有 Python 源里 `"command": "…"` / `["command"] = "…"` 的字面量，
 * 逐一断言落在 COMMAND_CATALOGUE（小写比较，与 map-action-handler 的
 * toLowerCase 查找一致）。
 *
 * 局限（triage 已知）：本不变量只抓「命令名缺失」，抓不到 #533（命令名存在
 * 但 params 形状错误）与 #534（校验器比 run body 窄）—— 那些有各自的形状
 * 测试兜底。
 */
const BACKEND_ROOT = join(process.cwd(), '..', 'app');

function collectPyFiles(dir: string, acc: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === '__pycache__' || entry.startsWith('.')) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      collectPyFiles(full, acc);
    } else if (extname(full) === '.py') {
      acc.push(full);
    }
  }
  return acc;
}

// 「command 键 + 可选引号/方括号 + 分隔符 + 字符串字面量」：
//   - "command": "query_features"      （dict 字面量）
//   - res_data["command"] = "…"        （键赋值，带 ] 与 =）
// 前置 lookbehind 排除 some_command 之类的变量名；值侧必须紧跟引号字面量，
// 排除 "command": result.get(...) 这类非字面量发射。
const COMMAND_EMISSION_RE =
  /(?<![A-Za-z0-9_])["']?command["']?\s*\]?\s*[=:]\s*["']([A-Za-z][A-Za-z0-9_]{0,48})["']/g;

describe('后端命令发射 ⊆ 前端命令目录（#535 幽灵命令不变量）', () => {
  it('app/ 里每个 "command" 字面量都在 COMMAND_CATALOGUE 中（大小写不敏感）', () => {
    const catalogue = new Set(
      (Object.keys(COMMAND_CATALOGUE) as string[]).map((k) => k.toLowerCase()),
    );
    const emissions = new Map<string, string[]>();

    for (const file of collectPyFiles(BACKEND_ROOT)) {
      const src = readFileSync(file, 'utf8');
      // Array.from: tsconfig.test.json 目标较低，RegExpStringIterator 不能直接
      // for-of（需 downlevelIteration）。
      for (const match of Array.from(src.matchAll(COMMAND_EMISSION_RE))) {
        const name = match[1].toLowerCase();
        if (!emissions.has(name)) emissions.set(name, []);
        emissions.get(name)!.push(file);
      }
    }

    expect(emissions.size).toBeGreaterThan(0);
    const ghosts = Array.from(emissions.keys()).filter((name) => !catalogue.has(name));
    expect(ghosts, `幽灵命令（前端目录缺失）: ${ghosts.join(', ')}`).toEqual([]);
  });

  it('占位完整性：目录本身能定位到后端 app/（测试环境路径正确）', () => {
    const pyFiles = collectPyFiles(BACKEND_ROOT);
    expect(pyFiles.length).toBeGreaterThan(100);
    // 我们关心的发射站点确实在扫描范围内
    const spatial = pyFiles.find((f) => f.includes('tools/spatial.py'));
    const dispatch = pyFiles.find((f) => f.includes('services/tool_dispatch_service.py'));
    expect(spatial).toBeTruthy();
    expect(dispatch).toBeTruthy();
  });
});