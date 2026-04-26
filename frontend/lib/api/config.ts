/**
 * Centralized API configuration
 * All API and WebSocket URLs should import from this module.
 */
export const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8001';
export const WS_BASE = process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8001';
