'use client';

/**
 * ResultsTab — GIS Analysis Result Workbench (left-tab, master-detail).
 *
 * One canonical result-detail experience with the list as its primary entry
 * point. The registry is session-scoped + bounded (spec §11/§16): we do not
 * create a persistence DB; we represent session-only results truthfully and
 * clear on session switch (wired in use-workspace-session).
 */
import { useMemo } from 'react';
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

  const selected = useMemo(
    () => (selectedResultId ? results.find((r) => r.id === selectedResultId) ?? null : null),
    [results, selectedResultId],
  );

  if (selected) {
    return (
      <ResultDetail
        result={selected}
        sessionId={sessionId}
        ownerToken={ownerToken}
        onBack={() => selectResult(null)}
        onSend={onSend}
      />
    );
  }

  return <ResultList results={results} selectedId={selectedResultId} onSelect={selectResult} />;
}

export default ResultsTab;
