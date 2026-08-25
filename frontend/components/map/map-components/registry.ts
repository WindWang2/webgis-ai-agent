'use client';
import type { ComponentRenderer } from './types';

const REGISTRY = new Map<string, ComponentRenderer>();

export function registerComponentRenderer(type: string, renderer: ComponentRenderer) {
  REGISTRY.set(type, renderer);
}

export function getComponentRenderer(type: string): ComponentRenderer | undefined {
  return REGISTRY.get(type);
}

export function hasComponentRenderer(type: string): boolean {
  return REGISTRY.has(type);
}

export function allRegisteredTypes(): string[] {
  return [...REGISTRY.keys()];
}

export function clearComponentRegistry() {
  REGISTRY.clear();
}
