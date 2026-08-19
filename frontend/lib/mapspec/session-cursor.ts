/** Session cursor for user MapSpec Mutations (#639). No session → chrome stays local. */

let sessionId: string | undefined;
let revision = 0;
let ownerToken: string | null = null;

export function setMapSpecSessionCursor(
  nextId: string | undefined,
  nextRevision = 0,
  nextOwnerToken: string | null = null,
): void {
  sessionId = nextId;
  revision = Number.isFinite(nextRevision) ? nextRevision : 0;
  ownerToken = nextOwnerToken;
}

export function getMapSpecSessionCursor(): {
  sessionId: string | undefined;
  revision: number;
  ownerToken: string | null;
} {
  return { sessionId, revision, ownerToken };
}

export function setMapSpecRevision(nextRevision: number): void {
  revision = nextRevision;
}
