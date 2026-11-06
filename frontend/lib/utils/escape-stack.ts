/** One Escape action per key, with explicit tool priority. */
const handlers = new Set<{ priority: number; handle: (event: KeyboardEvent) => boolean }>();
const dispatch = (event: KeyboardEvent) => {
  if (event.key !== 'Escape' || document.querySelector('[role=dialog][aria-modal=true]')) return;
  for (const entry of [...handlers].sort((a, b) => b.priority - a.priority)) {
    if (entry.handle(event)) { event.preventDefault(); event.stopImmediatePropagation(); break; }
  }
};
export function registerEscapeHandler(priority: number, handle: (event: KeyboardEvent) => boolean): () => void {
  if (handlers.size === 0) window.addEventListener('keydown', dispatch, true);
  const entry = { priority, handle }; handlers.add(entry);
  return () => { handlers.delete(entry); if (handlers.size === 0) window.removeEventListener('keydown', dispatch, true); };
}
