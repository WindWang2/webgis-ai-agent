/**
 * Skills API client.
 *
 * F-FE-3 migration: previously raw fetch + plain Error. Now uses the Fast
 * Path (in-flight dedup + 5s LRU) since the skills list is mount-time state
 * shared by every panel that surfaces skill metadata.
 */

import { fastGet } from './get-fast-path';

export interface Skill {
  name: string;
  description: string;
}

interface SkillsResponse {
  skills: Skill[];
}

export async function getSkills(opts?: { forceRefresh?: boolean; signal?: AbortSignal }): Promise<Skill[]> {
  const result = await fastGet<SkillsResponse>('/api/v1/chat/skills', {
    forceRefresh: opts?.forceRefresh,
    signal: opts?.signal,
    label: 'Skills API error',
  });
  return result.data.skills || [];
}
