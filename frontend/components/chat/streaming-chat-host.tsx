"use client";

import React, { useEffect } from "react";
import { useSSEStream } from "@/lib/hooks/use-sse-stream";
import { ContextPanel } from "@/components/layout/context-panel";
import type { MapActionPayload } from "@/lib/types";
import type { SessionPlanViewState } from "@/lib/session/session-plan-delta";
import { useChatStore } from "@/lib/store/useChatStore";

export interface StreamingChatHostProps {
  sessionId: string | undefined;
  setSessionId: (id: string | undefined) => void;
  sessionIdRef: React.RefObject<string | undefined>;
  dispatchAction: (action: MapActionPayload) => void;
  getMapSnapshot: () => unknown;
  userLocation: { lng: number; lat: number; accuracy?: number } | null;
  sessionTokenRef: React.RefObject<string | null>;
  rememberSessionToken: (sid: string, token: string) => void;
  getSessionTokenFor: (sid: string) => string | null;
  activeSessionToken: string | null;
  sessionPlanView: SessionPlanViewState;
  applySessionPlanEvent: (eventName: string, data: unknown) => void;
  onRegisterSetMessages?: (setter: (updater: any) => void) => void;
  onRegisterViewportChange?: (fn: (center: [number, number], zoom: number, bearing: number, pitch: number) => void) => void;
  onMessagesChange?: (messages: any[]) => void;
}

/**
 * FRONT-05: Decoupled streaming chat host component.
 * Owns the high-frequency streaming messages and token state via useSSEStream,
 * isolating re-render cascades during AI token generation to this subtree rather
 * than forcing the root page (Home) and its sibling layouts to re-render.
 */
export function StreamingChatHost({
  sessionId,
  setSessionId,
  sessionIdRef,
  dispatchAction,
  getMapSnapshot,
  userLocation,
  sessionTokenRef,
  rememberSessionToken,
  getSessionTokenFor,
  activeSessionToken,
  sessionPlanView,
  applySessionPlanEvent,
  onRegisterSetMessages,
  onRegisterViewportChange,
  onMessagesChange,
}: StreamingChatHostProps) {
  const {
    messages,
    setMessages,
    aiStatus,
    handleSend,
    handlePlanAction,
    bridge,
    agentRuntime,
  } = useSSEStream(
    sessionId,
    setSessionId,
    sessionIdRef,
    dispatchAction,
    getMapSnapshot,
    userLocation,
    sessionTokenRef,
    rememberSessionToken,
    getSessionTokenFor,
    applySessionPlanEvent
  );

  useEffect(() => {
    onRegisterSetMessages?.(setMessages);
  }, [onRegisterSetMessages, setMessages]);

  useEffect(() => {
    onRegisterViewportChange?.(bridge.onViewportChange);
  }, [onRegisterViewportChange, bridge.onViewportChange]);

  useEffect(() => {
    onMessagesChange?.(messages);
    useChatStore.getState().setMessages(messages);
  }, [messages, onMessagesChange]);

  return (
    <ContextPanel
      messages={messages}
      aiStatus={aiStatus}
      onSend={handleSend}
      onCancel={bridge.cancel}
      sessionId={sessionId ?? null}
      ownerToken={activeSessionToken}
      onPlanAction={handlePlanAction}
      agentRuntime={agentRuntime}
      sessionPlan={sessionPlanView}
    />
  );
}
