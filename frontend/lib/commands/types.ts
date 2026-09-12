'use client';

import type { ComponentType } from 'react';

/**
 * 命令注册框架（ADR-0147）——供本线命令面板与后续 D–H 线各自贡献命令的公共入口。
 *
 * 设计约束：
 * - 纯数据注册表，不依赖 React / zustand（与 lib/workbench/undo.ts 的外部 store
 *   模式一致），组件可通过 useRegisterCommands 在挂载期注册、卸载期反注册。
 * - 注册是 append-only 语义：registerCommands 返回反注册函数，禁止运行中替换
 *   他人已注册命令（多线并发存活规则，§8）。
 * - `when` 谓词在面板过滤时求值，命令本体不做权限/可达性判断。
 */

/** 命令执行上下文。目前为空壳，为 D–H 线接入保留扩展位（如传 map 实例）。 */
export interface CommandExecutionContext {
  /** 命令来源：面板回车 / 快捷键 / 测试直调。 */
  source: 'palette' | 'shortcut' | 'api';
}

export interface CommandParamSpec {
  /** 进入参数模式时输入框上方的提示文案。 */
  prompt: string;
  placeholder?: string;
}

export interface CommandDef {
  /** 全局唯一 id，命名空间式：`<域>.<动作>`，如 `view.theme.toggle`。 */
  id: string;
  /** 面板展示标题（中文）。 */
  title: string;
  /** 面板分组名（中文），同组按注册顺序排列。 */
  group: string;
  /** 额外搜索关键词（空格分隔），参与模糊匹配。 */
  keywords?: string;
  /** 快捷键串，如 `ctrl+k`、`ctrl+shift+p`、`?`。仅描述性元数据（总览展示 +
   * 冲突检测）；注册表不自动派发——实际按键绑定由命令方自行挂 listener
   * （如 edit.undo 复用 use-undo.ts 既有全局键，panel.palette 由
   * useCommandPaletteHotkeys 实绑），避免同一键双触发。 */
  shortcut?: string;
  /** 可选图标（lucide 组件），面板渲染用；注册表本体不感知 React 树。 */
  icon?: ComponentType<{ size?: number | string; className?: string }>;
  /** 可达性谓词：返回 false 时面板隐藏该命令、快捷键不触发。 */
  when?: () => boolean;
  /** 执行器。参数化命令通过 paramSpec 声明，run 接收用户输入。 */
  run: (ctx: CommandExecutionContext, input?: string) => void | Promise<void>;
  /** 参数化命令：声明后面板回车先收集输入再执行（加深杠杆，v1 已支持）。 */
  paramSpec?: CommandParamSpec;
  /** 面板次级说明。 */
  description?: string;
}

/** 快捷键冲突报告：同一归一化快捷键被 ≥2 条命令注册。 */
export interface ShortcutConflict {
  shortcut: string;
  commandIds: string[];
}
