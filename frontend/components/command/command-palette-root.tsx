'use client';

import { useEffect } from 'react';
import { FileImage, FileText, FileCode2 } from 'lucide-react';
import { registerCommands } from '@/lib/commands/registry';
import { builtinCommands } from '@/lib/commands/builtin';
import { useCommandPaletteHotkeys } from '@/lib/hooks/use-command-palette';
import { useMapAction } from '@/lib/contexts/map-action-context';
import { useHudStore } from '@/lib/store/useHudStore';
import { useAuthUser } from '@/lib/auth/use-auth-user';
import { CommandPalette } from './command-palette';
import { ShortcutOverview } from './shortcut-overview';

/**
 * 命令面板挂载根（ADR-0147）：注册内建静态命令 + dispatch 依赖命令，
 * 绑定全局热键（Ctrl/Cmd+K、`?`），渲染面板与快捷键总览。
 *
 * 必须挂在 MapActionProvider 子树内（导出命令需要 useMapAction）。
 * app/story 与后续 D–H 线可各自 useRegisterCommands 贡献命令。
 */
export function CommandPaletteRoot() {
  const { dispatchAction } = useMapAction();
  const authUser = useAuthUser();

  useCommandPaletteHotkeys();

  useEffect(() => registerCommands(builtinCommands), []);

  useEffect(() => {
    const exportCommand = (format: 'png' | 'svg' | 'pdf') => {
      const { exportSettings } = useHudStore.getState();
      dispatchAction({
        command: 'export_map',
        params: { ...exportSettings, format },
      });
    };
    return registerCommands([
      {
        id: 'file.export.png',
        title: '发布并导出 PNG',
        group: '文件',
        keywords: 'export png tupian export',
        icon: FileImage,
        // #469 契约：POST /api/v1/export 需认证——未登录时命令隐藏而不是
        // 让用户点了吃 401 toast（与制图面板按钮同守卫）。
        when: () => Boolean(authUser),
        description: '需登录；按当前制图面板设置渲染',
        run: () => exportCommand('png'),
      },
      {
        id: 'file.export.svg',
        title: '发布并导出 SVG',
        group: '文件',
        keywords: 'export svg vector',
        icon: FileCode2,
        when: () => Boolean(authUser),
        run: () => exportCommand('svg'),
      },
      {
        id: 'file.export.pdf',
        title: '发布并导出 PDF',
        group: '文件',
        keywords: 'export pdf',
        icon: FileText,
        when: () => Boolean(authUser),
        run: () => exportCommand('pdf'),
      },
    ]);
  }, [dispatchAction, authUser]);

  return (
    <>
      <CommandPalette />
      <ShortcutOverview />
    </>
  );
}
