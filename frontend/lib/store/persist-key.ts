/**
 * Isomorphic home for the hud-store persist key.
 *
 * MUST stay a plain (non-"use client") module: app/layout.tsx embeds this
 * value into the inline no-flash theme bootstrap, and layout runs as a React
 * Server Component — importing any value from a "use client" module there
 * silently yields `undefined` at SSR (the RSC boundary only carries component
 * references), which turned the bootstrap into `localStorage.getItem(undefined)`
 * and made dark-mode flash white on every reload (caught by journey 5,
 * quality-e2e-v9). Import the key from here in both places.
 */
export const PERSIST_KEY = 'geoagent-settings';
