import { API_BASE } from './config';

export interface Skill {
  name: string;
  description: string;
}

export async function getSkills(): Promise<Skill[]> {
  const res = await fetch(`${API_BASE}/api/v1/chat/skills`);
  if (!res.ok) throw new Error(`Failed to fetch skills: ${res.status}`);
  const data = await res.json();
  return data.skills || [];
}
