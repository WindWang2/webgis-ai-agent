import { describe, it, expect } from 'vitest';
import { agentRuntimeLabel, parseAgentRuntime } from './agent-runtime';

describe('agent-runtime', () => {
  it('parses only pi / chatengine', () => {
    expect(parseAgentRuntime('pi')).toBe('pi');
    expect(parseAgentRuntime('chatengine')).toBe('chatengine');
    expect(parseAgentRuntime('ChatEngine')).toBeNull();
    expect(parseAgentRuntime(undefined)).toBeNull();
  });

  it('labels the host for the chat header', () => {
    expect(agentRuntimeLabel('pi')).toBe('powered by Pi');
    expect(agentRuntimeLabel('chatengine')).toBe('powered by ChatEngine');
  });
});
