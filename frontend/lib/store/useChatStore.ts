import { create } from 'zustand';

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: Date | number | null;
  isThinking?: boolean;
  charts?: unknown[];
  resultId?: string;
  plan?: unknown;
  toolCalls?: unknown[];
  [key: string]: unknown;
}

export interface ChatStoreState {
  messages: ChatMessage[];
  streamingToken: string;
  setMessages: (messages: ChatMessage[] | ((prev: ChatMessage[]) => ChatMessage[])) => void;
  setStreamingToken: (token: string) => void;
  clearMessages: () => void;
}

/**
 * FRONT-05: Dedicated chat store to decouple streaming token and chat message
 * state from the page root component (Home), avoiding 60fps subtree re-renders
 * during token streaming.
 */
export const useChatStore = create<ChatStoreState>((set) => ({
  messages: [],
  streamingToken: '',
  setMessages: (updater) =>
    set((state) => ({
      messages: typeof updater === 'function' ? updater(state.messages) : updater,
    })),
  setStreamingToken: (token) => set({ streamingToken: token }),
  clearMessages: () => set({ messages: [], streamingToken: '' }),
}));
