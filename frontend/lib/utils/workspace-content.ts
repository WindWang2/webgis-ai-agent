/**
 * #553：「新建会话」会一键清空整个工作区（图层 / 标注 / 操作日志 / 结果 /
 * transcript），此前无任何确认。此函数判断当前是否有值得确认的内容：
 * 任何图层 / 标注 / 操作日志 / 结果，或任何非初始欢迎气泡的消息。
 *
 * 纯函数，组件与测试共用；内容本身服务端仍保留（可从历史记录找回），
 * 这里只决定是否值得弹一次确认。
 */

/** 页面初始欢迎气泡的消息 id（app/page.tsx startFreshSession 写入）。 */
const WELCOME_MESSAGE_ID = '1';

export function hasWorkspaceContent(
  messages: Array<{ id?: unknown }>,
  layers: unknown[],
  annotations: unknown[],
  opsLog: unknown[],
  results: unknown[],
): boolean {
  if (layers.length > 0 || annotations.length > 0 || opsLog.length > 0 || results.length > 0) {
    return true;
  }
  // 仅初始欢迎气泡（id '1'）不算"要丢的内容"；其它任何消息（用户输入、
  // 恢复的真实会话消息）都算。
  return messages.some((m) => m?.id !== WELCOME_MESSAGE_ID);
}
