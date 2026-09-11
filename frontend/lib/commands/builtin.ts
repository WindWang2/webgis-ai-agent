'use client';

import { redo, undo } from '@/lib/workbench/undo';
import { useHudStore } from '@/lib/store/useHudStore';
import type { LeftTab } from '@/lib/store/hud-types';
import type { WorkbenchMode } from '@/lib/store/slices/workbenchSlice';
import { useCommandPaletteStore } from '@/lib/hooks/use-command-palette';
import type { CommandDef } from './types';

/**
 * 内建静态命令种子（ADR-0147）。
 *
 * 只收录不依赖 React 上下文的动作（store / undo 栈 / 面板开关）。
 * 依赖 dispatchAction / 会话句柄的命令在 CommandContributions 与 page 层
 * 动态注册——这是 D–H 线接入时应遵循的同款模式。
 */

const TAB_DEFS: Array<{ tab: LeftTab; label: string; keywords?: string }> = [
  { tab: 'chat', label: '对话', keywords: 'chat chat' },
  { tab: 'project', label: '项目', keywords: 'project workflow' },
  { tab: 'data_sources', label: '数据', keywords: 'data source fabric catalog shuju' },
  { tab: 'layers', label: '图层', keywords: 'layers tuceng' },
  { tab: 'components', label: '组件', keywords: 'components dock' },
  { tab: 'analysis', label: '分析', keywords: 'analysis tools' },
  { tab: 'tasks', label: '任务', keywords: 'tasks jobs' },
  { tab: 'results', label: '结果', keywords: 'results artifacts' },
  { tab: 'export_layout', label: '制图', keywords: 'export layout cartography map studio' },
];

function buildTabCommands(): CommandDef[] {
  return TAB_DEFS.map(({ tab, label, keywords }) => ({
    id: `nav.tab.${tab}`,
    title: `打开${label}面板`,
    group: '导航',
    keywords: `${label} tab ${keywords ?? ''}`,
    run: () => {
      useHudStore.getState().setActiveLeftTab(tab);
    },
  }));
}

const MODE_DEFS: Array<{ mode: WorkbenchMode; label: string; keywords: string }> = [
  { mode: 'explore', label: '探索', keywords: 'explore explore' },
  { mode: 'analyze', label: '分析', keywords: 'analyze fenxi' },
  { mode: 'compose', label: '制图', keywords: 'compose zhoutu compose' },
];

function buildModeCommands(): CommandDef[] {
  return MODE_DEFS.map(({ mode, label, keywords }) => ({
    id: `mode.switch.${mode}`,
    title: `切换到${label}模式`,
    group: '模式',
    keywords: `mode ${label} ${keywords}`,
    run: () => {
      useHudStore.getState().setWorkbenchMode(mode, 'user');
    },
  }));
}

function buildViewCommands(): CommandDef[] {
  return [
    {
      id: 'view.theme.toggle',
      title: '切换明暗主题',
      group: '视图',
      keywords: 'theme dark light zhuti yanshe',
      run: () => {
        const { theme, setTheme } = useHudStore.getState();
        setTheme(theme === 'dark' ? 'light' : 'dark');
      },
    },
    {
      // 命名注意：id 不能含 "map." 子串——maplibre-mock-surface 元测试对
      // lib/components 源文本做 \bmap\. 静态扫描（#404），字符串字面量也会命中。
      id: 'view.threed.toggle',
      title: '切换 2D/3D 视角',
      group: '视图',
      keywords: '3d 2d perspective shijiao',
      run: () => {
        const { is3D, setIs3D } = useHudStore.getState();
        setIs3D(!is3D);
      },
    },
    {
      id: 'view.panel.toggle-left',
      title: '折叠/展开左侧面板',
      group: '视图',
      keywords: 'panel collapse zhedie',
      run: () => {
        useHudStore.getState().toggleLeftPanel();
      },
    },
  ];
}

function buildPanelCommands(): CommandDef[] {
  return [
    {
      id: 'panel.palette',
      title: '打开命令面板',
      group: '面板',
      keywords: 'command palette mingling search',
      shortcut: 'mod+k',
      // 热键由 useCommandPaletteHotkeys 实绑（防双触发：registry 的 shortcut
      // 字段是描述性元数据，不自动派发——见 types.ts 契约注释）。
      run: () => useCommandPaletteStore.getState().open('palette'),
    },
    {
      id: 'panel.history',
      title: '打开历史会话',
      group: '面板',
      keywords: 'history lishi session',
      run: () => {
        useHudStore.getState().setHistoryOpen(true);
      },
    },
    {
      id: 'panel.templates',
      title: '打开模板库',
      group: '面板',
      keywords: 'template moban gallery',
      run: () => {
        useHudStore.getState().setTemplatesOpen(true);
      },
    },
    {
      id: 'panel.tweaks',
      title: '打开 UI 调整',
      group: '面板',
      keywords: 'tweaks ui appearance',
      run: () => useHudStore.getState().setTweaksOpen(true),
    },
    {
      id: 'panel.settings',
      title: '打开设置',
      group: '面板',
      keywords: 'settings shezhi preferences',
      run: () => useHudStore.getState().setSettingsOpen(true),
    },
    {
      id: 'panel.shortcuts',
      title: '快捷键总览',
      group: '帮助',
      keywords: 'shortcuts kuaijianjian keyboard help',
      shortcut: '?',
      run: () => useCommandPaletteStore.getState().open('shortcuts'),
    },
  ];
}

function buildEditCommands(): CommandDef[] {
  return [
    {
      id: 'edit.undo',
      title: '撤销',
      group: '编辑',
      keywords: 'undo chexiao',
      shortcut: 'mod+z',
      run: () => {
        undo();
      },
    },
    {
      id: 'edit.redo',
      title: '重做',
      group: '编辑',
      keywords: 'redo chongzuo',
      shortcut: 'mod+shift+z',
      run: () => {
        redo();
      },
    },
  ];
}

/** 模块加载即注册的静态种子；dispatch 依赖命令见 CommandContributions。 */
export const builtinCommands: CommandDef[] = [
  ...buildTabCommands(),
  ...buildModeCommands(),
  ...buildViewCommands(),
  ...buildPanelCommands(),
  ...buildEditCommands(),
];
