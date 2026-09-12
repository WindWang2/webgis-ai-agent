'use client';

/**
 * 快捷键归一化与事件匹配。
 *
 * 归一化形态：小写修饰符按 `ctrl+alt+shift+meta <key>` 排序拼接；
 * `mod` 是平台无关写法（mac=meta，其余=ctrl），归一化保留 `mod` 以便
 * 跨平台注册同一串；事件匹配时把平台实际修饰符折算回 `mod`。
 */

export interface ParsedShortcut {
  ctrl: boolean;
  alt: boolean;
  shift: boolean;
  meta: boolean;
  /** 归一化主键（小写）；`?` 这类符号键保持字面。 */
  key: string;
}

export function isMacPlatform(): boolean {
  if (typeof navigator === 'undefined') return false;
  return /mac|iphone|ipad/i.test(navigator.platform || navigator.userAgent || '');
}

export function parseShortcut(combo: string): ParsedShortcut {
  const parts = combo
    .toLowerCase()
    .split('+')
    .map((p) => p.trim())
    .filter(Boolean);
  const key = parts[parts.length - 1] ?? '';
  const mods = new Set(parts.slice(0, -1));
  const mod = mods.has('mod');
  return {
    ctrl: mods.has('ctrl') || (!isMacPlatform() && mod),
    alt: mods.has('alt'),
    shift: mods.has('shift'),
    meta: mods.has('meta') || (isMacPlatform() && mod),
    key,
  };
}

/** 归一化注册串：'Ctrl+K' → 'ctrl+k'，修饰符按固定序。 */
export function normalizeShortcut(combo: string): string {
  const p = parseShortcut(combo);
  const mods: string[] = [];
  if (p.ctrl) mods.push('ctrl');
  if (p.alt) mods.push('alt');
  if (p.shift) mods.push('shift');
  if (p.meta) mods.push('meta');
  return [...mods, p.key].join('+');
}

/** 展示串：mac 用 ⌘/⌥/⇧，其余用 Ctrl/Alt/Shift。 */
export function formatShortcut(combo: string): string {
  const p = parseShortcut(combo);
  const mac = isMacPlatform();
  const key = p.key.length === 1 ? p.key.toUpperCase() : p.key;
  if (mac) {
    const mods =
      (p.ctrl ? '⌃' : '') + (p.alt ? '⌥' : '') + (p.shift ? '⇧' : '') + (p.meta ? '⌘' : '');
    return `${mods}${key}`;
  }
  const mods =
    (p.ctrl ? 'Ctrl+' : '') + (p.alt ? 'Alt+' : '') + (p.shift ? 'Shift+' : '') + (p.meta ? 'Win+' : '');
  return `${mods}${key}`;
}

function eventHasKey(e: KeyboardEvent, key: string): boolean {
  const eKey = e.key.toLowerCase();
  if (eKey === key) return true;
  // numkey/别名容错：CommandOrControl 等历史写法不注册即可，这里只兜
  // 常见空格与方向键别名。
  if (key === 'space' && eKey === ' ') return true;
  if (key === 'plus' && (eKey === '+' || (eKey === '=' && e.shiftKey))) return true;
  return false;
}

/** KeyboardEvent 是否命中（已归一化的）快捷键。 */
export function matchesShortcut(e: KeyboardEvent, normalized: string): boolean {
  const p = parseShortcut(normalized);
  // mod 在解析时已按平台折算进 ctrl/meta；若注册串直接写了非本平台修饰符
  // （如 mac 上注册 ctrl+k），仍按字面匹配，不做二次折算。
  if (p.ctrl !== e.ctrlKey) return false;
  if (p.alt !== e.altKey) return false;
  if (p.meta !== e.metaKey) return false;
  // shift 参与符号键形变（`?` 即 shift+/），仅当主键是字母/数字时严格比对。
  if (p.key.length === 1 && /[a-z0-9]/.test(p.key)) {
    if (p.shift !== e.shiftKey) return false;
    return eventHasKey(e, p.key);
  }
  return eventHasKey(e, p.key);
}

const EDITABLE_TAGS = new Set(['input', 'textarea', 'select']);

/** 焦点是否在可编辑元素内（快捷键默认避让）。 */
export function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (EDITABLE_TAGS.has(target.tagName.toLowerCase())) return true;
  // contenteditable 属性直读兜底（jsdom 不维护 isContentEditable 反射）。
  if (target.isContentEditable) return true;
  return target.getAttribute('contenteditable') === 'true';
}
