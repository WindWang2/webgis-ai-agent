'use client';

/**
 * ResultsTab — GIS Analysis Result Workbench (left-tab, master-detail).
 *
 * One canonical result-detail experience with the list as its primary entry
 * point. The registry is session-scoped + bounded (spec §11/§16): we do not
 * create a persistence DB; we represent session-only results truthfully and
 * clear on session switch (wired in use-workspace-session).
 *
 * Focus contract (drill-in navigation): activating a row moves focus into the
 * detail (its back button), and going back restores focus to the row that was
 * opened — keyboard users never lose their place in the list. The ring only
 * paints for keyboard-origin focus (`:focus-visible`), so mouse users see no
 * change.
 */
import { useCallback, useMemo, useState } from 'react';
import { useHudStore } from '@/lib/store/useHudStore';
import { ResultList } from '@/components/sidebar/results/result-list';
import { ResultDetail } from '@/components/sidebar/results/result-detail';

interface ResultsTabProps {
  sessionId?: string | null;
  ownerToken?: string | null;
  onSend: (text: string) => void;
}

export function ResultsTab({ sessionId, ownerToken, onSend }: ResultsTabProps) {
  const results = useHudStore((s) => s.results);
  const selectedResultId = useHudStore((s) => s.selectedResultId);
  const selectResult = useHudStore((s) => s.selectResult);

  // Which row Back should restore focus to (the row unmounts while the detail
  // is open, so the id has to survive here). Cleared once the list consumed it.
  const [restoreFocusId, setRestoreFocusId] = useState<string | null>(null);

  const selected = useMemo(
    () => (selectedResultId ? results.find((r) => r.id === selectedResultId) ?? null : null),
    [results, selectedResultId],
  );

  const handleBack = useCallback(() => {
    if (selectedResultId) setRestoreFocusId(selectedResultId);
    selectResult(null);
  }, [selectedResultId, selectResult]);

  if (selected) {
    return (
      <ResultDetail
        result={selected}
        sessionId={sessionId}
        ownerToken={ownerToken}
        onBack={handleBack}
        onSend={onSend}
      />
    );
  }

  return (
    <ResultList
      results={results}
      selectedId={selectedResultId}
      onSelect={selectResult}
      restoreFocusId={restoreFocusId}
      onRestoredFocus={() => setRestoreFocusId(null)}
    />
  );
}

export default ResultsTab;
