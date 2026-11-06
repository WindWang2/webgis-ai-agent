/**
 * Select PR smoke from immutable event commits, never a moving branch or HEAD.
 * A queued run may start after merge/force-push. Only a verified event diff may
 * skip; missing history, unexpected checkout, or a broken mapper must run.
 */
import { spawnSync } from 'node:child_process';
import { appendFileSync, readFileSync, writeFileSync } from 'node:fs';
import { mapChangedFiles } from './changed-lanes.mjs';

function git(...args) {
  const result = spawnSync('git', ['--no-replace-objects', ...args], {
    encoding: 'utf8',
    timeout: 30_000,
    maxBuffer: 16 * 1024 * 1024,
  });
  if (result.error || result.status !== 0) {
    throw new Error(`git ${args[0]} unavailable`);
  }
  return result.stdout;
}

function sha(value) {
  if (typeof value !== 'string' || !/^[0-9a-f]{40}$/.test(value) || /^0+$/.test(value)) {
    throw new Error('missing or invalid event commit');
  }
  return value;
}

function ensureCommit(commit) {
  try {
    if (git('cat-file', '-t', commit).trim() === 'commit') return;
  } catch {
    // Full checkout normally has these objects. Fetch only the immutable SHA,
    // never a branch name or the current PR ref, if an event object is missing.
  }
  git('fetch', '--no-tags', '--no-recurse-submodules', 'origin', commit);
  if (git('cat-file', '-t', commit).trim() !== 'commit') {
    throw new Error('event object is not a commit');
  }
}

function decide() {
  // No push trigger today. If added later, branch creation, deletion and forced
  // pushes must not reuse PR merge-base semantics. All non-PR events run smoke.
  if (process.env.GITHUB_EVENT_NAME !== 'pull_request') {
    return { run: true, reason: 'non-PR event' };
  }

  const event = JSON.parse(readFileSync(process.env.GITHUB_EVENT_PATH, 'utf8'));
  const base = sha(event?.pull_request?.base?.sha);
  const head = sha(event?.pull_request?.head?.sha);
  const checkout = sha(process.env.GITHUB_SHA);
  if (git('rev-parse', '--verify', 'HEAD').trim() !== checkout) {
    throw new Error('checkout does not match event commit');
  }
  ensureCommit(base);
  ensureCommit(head);
  if (git('rev-parse', '--is-shallow-repository').trim() !== 'false') {
    throw new Error('shallow history');
  }

  // actions/checkout tests the synthetic merge by default. Accept a direct PR
  // head too, but do not silently select from one PR while testing another tree.
  if (checkout !== head) {
    const parents = git('show', '-s', '--format=%P', checkout).trim().split(' ');
    if (parents.length !== 2 || parents[0] !== base || parents[1] !== head) {
      throw new Error('checkout is not the event PR head or merge');
    }
  }
  const mergeBases = git('merge-base', '--all', base, head).trim().split('\n');
  if (mergeBases.length !== 1) throw new Error('ambiguous merge base');
  const mergeBase = sha(mergeBases[0]);
  // --no-renames includes BOTH paths when a file moves out of a mapped prefix.
  // NUL delimiters preserve spaces, newlines, Unicode and Git-quoted filenames.
  const paths = git('diff', '--no-ext-diff', '--no-textconv', '--name-only',
    '--no-renames', '-z', mergeBase, head, '--').split('\0').filter(Boolean);
  const decision = mapChangedFiles(paths);
  if (!decision || typeof decision.run !== 'boolean' || typeof decision.reason !== 'string') {
    throw new Error('invalid lane mapper decision');
  }
  return { ...decision, base, head, checkout, mergeBase };
}

let decision;
try {
  decision = decide();
} catch (error) {
  decision = { run: true, reason: `${error.message} (fail closed)` };
}
writeFileSync('lanes.json', `${JSON.stringify(decision)}\n`);
// Only an actual boolean false can skip. Never coerce a mapper's string/null.
appendFileSync(process.env.GITHUB_OUTPUT, `run=${decision.run}\n`);
