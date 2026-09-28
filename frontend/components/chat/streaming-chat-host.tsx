"use client";

import React, { useEffect } from "react";
import { useSSEStream } from "@/lib/hooks/use-sse-stream";
import { ContextPanel } from "@/components/layout/context-panel";
import type { LayoutMode } from "@/lib/hooks/use-layout-mode";
import type { MapActionPayload } from "@/lib/types";
import type { SessionPlanViewState } from "@/lib/session/session-plan-delta";

export interface StreamingChatHostProps {
  /** P5 三档布局档位（透传给 ContextPanel 决定 dock/sheet 形态）。 */
  layoutMode?: LayoutMode;
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
  onRegisterViewportChange,
  onMessagesChange,
  layoutMode = 'desktop',
}: StreamingChatHostProps) {
  const {
    messages,
    aiStatus,
    handleSend,
    handlePlanAction,
    bridge,
    agentRuntime,
  } = useSSEStream({
    sessionId,
    setSessionId,
    sessionIdRef,
    dispatchAction,
    getMapSnapshot,
    userLocation,
    sessionTokenRef,
    rememberSessionToken,
    getSessionToken: getSessionTokenFor,
    onSessionPlanEvent: applySessionPlanEvent,
  });

  useEffect(() => {
    onRegisterViewportChange?.(bridge.onViewportChange);
  }, [onRegisterViewportChange, bridge.onViewportChange]);

  // H03 / #1554：useChatStore 是消息单一 owner —— 本组件经 store 订阅读，
  // 不再做「hook useState → useEffect 镜像写 store」的双 owner 滞后副本。
  // onMessagesChange 保留（page 的 messagesRef 消费契约）。
  useEffect(() => {
    onMessagesChange?.(messages);
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
      layoutMode={layoutMode}
    />
  );
}
