import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { RagInsightCard } from './rag-insight-card';

const setRagInsight = vi.fn();
let mockRagInsight: { title: string; content: string; source?: string } | null = null;

vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: (selector: (s: any) => any) => selector({
    ragInsight: mockRagInsight,
    setRagInsight,
  }),
}));

/* eslint-disable @typescript-eslint/no-require-imports */
vi.mock('framer-motion', () => {
  const fm = require('../../test/__mocks__/framer-motion');
  return { motion: fm.motion, AnimatePresence: fm.AnimatePresence };
});

describe('RagInsightCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockRagInsight = null;
  });

  it('renders nothing when ragInsight is null', () => {
    const { container } = render(<RagInsightCard />);
    expect(container.innerHTML).toBe('');
  });

  it('renders insight title and content', () => {
    mockRagInsight = { title: 'Test Title', content: 'Test body content' };
    render(<RagInsightCard />);
    expect(screen.getByText('Test Title')).toBeInTheDocument();
    expect(screen.getByText('Test body content')).toBeInTheDocument();
  });

  it('renders source when provided', () => {
    mockRagInsight = { title: 'T', content: 'C', source: 'osm-data' };
    render(<RagInsightCard />);
    expect(screen.getByText(/osm-data/)).toBeInTheDocument();
  });

  it('does not render source section when source is undefined', () => {
    mockRagInsight = { title: 'T', content: 'C' };
    render(<RagInsightCard />);
    expect(screen.queryByText(/来源/)).not.toBeInTheDocument();
  });

  it('calls setRagInsight(null) when dismiss button clicked', () => {
    mockRagInsight = { title: 'T', content: 'C' };
    render(<RagInsightCard />);
    const buttons = screen.getAllByRole('button');
    fireEvent.click(buttons[0]);
    expect(setRagInsight).toHaveBeenCalledWith(null);
  });

  it('shows RAG Insight header label', () => {
    mockRagInsight = { title: 'T', content: 'C' };
    render(<RagInsightCard />);
    expect(screen.getByText('RAG Insight')).toBeInTheDocument();
  });
});
