"""Run the actual workflow selector against real, isolated Git histories.

A successful diff against a live base is not safe: once a queued PR is merged,
that base contains the PR and the diff becomes empty. Only an immutable event
comparison may skip smoke; failures and unknown checkout identities must run.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "quality-e2e.yml"
TOOLS = Path("frontend/e2e/tools")
SELECTOR = TOOLS / "select-journey-smoke.mjs"
MAPPER = TOOLS / "changed-lanes.mjs"
FRONTEND = "frontend/components/widget.ts"
DOC = "docs/guide.md"
ZERO_SHA = "0" * 40


def _workflow() -> dict:
    # BaseLoader preserves the YAML 1.2 Actions key "on" (not YAML 1.1 True).
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _job() -> dict:
    return _workflow()["jobs"]["journey-smoke"]


def _decide_step() -> dict:
    return next(step for step in _job()["steps"] if step.get("id") == "lanes")


class Repository:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir()
        self.env = {
            **os.environ,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
        self.git("init", "-q", "-b", "master")
        self.git("config", "user.email", "ci-selector@example.test")
        self.git("config", "user.name", "Selector test")
        (root / TOOLS).mkdir(parents=True)
        for path in (SELECTOR, MAPPER):
            shutil.copyfile(REPO_ROOT / path, root / path)
        self.base = self.commit({FRONTEND: "original widget\n", DOC: "guide\n"})

    def git(self, *args: str) -> str:
        return subprocess.check_output(
            ["git", *args], cwd=self.root, env=self.env, text=True, stderr=subprocess.PIPE,
        ).strip()

    def commit(self, changes: dict[str, str | None]) -> str:
        for name, text in changes.items():
            path = self.root / name
            if text is None:
                path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
        self.git("add", "--all")
        self.git("commit", "-qm", "fixture", "--allow-empty")
        return self.git("rev-parse", "HEAD")

    def pr(self, changes: dict[str, str | None] | None = None) -> str:
        self.git("checkout", "-qb", "pr", self.base)
        return self.commit(changes if changes is not None else {FRONTEND: "PR change\n"})

    def merge(self, head: str, base: str | None = None) -> str:
        self.git("checkout", "--detach", "-q", base or self.base)
        self.git("merge", "--no-ff", "-qm", "event merge", head)
        return self.git("rev-parse", "HEAD")

    def select(self, head: str, *, base: str | None = None,
               checkout: str | None = None, event_name: str = "pull_request",
               payload: dict | None = None, raw_event: str | None = None,
               event_path: Path | None = None) -> dict:
        event_file = self.root.parent / "event.json"
        output_file = self.root.parent / "output.txt"
        output_file.write_text("")
        event = payload if payload is not None else {
            "pull_request": {"base": {"sha": base or self.base}, "head": {"sha": head}},
        }
        event_file.write_text(raw_event if raw_event is not None else json.dumps(event))
        env = {
            **self.env,
            "GITHUB_EVENT_NAME": event_name,
            "GITHUB_EVENT_PATH": str(event_path or event_file),
            "GITHUB_SHA": checkout if checkout is not None else self.git("rev-parse", "HEAD"),
            "GITHUB_OUTPUT": str(output_file),
        }
        subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", _decide_step()["run"]],
            cwd=self.root, env=env, check=True, capture_output=True, text=True,
        )
        decision = json.loads((self.root / "lanes.json").read_text())
        assert isinstance(decision["run"], bool)
        assert output_file.read_text().splitlines() == [f"run={str(decision['run']).lower()}"]
        return decision


@pytest.fixture
def repo(tmp_path: Path) -> Repository:
    missing = [binary for binary in ("git", "node", "bash") if shutil.which(binary) is None]
    if missing:
        pytest.skip(f"selector integration needs {', '.join(missing)}")
    return Repository(tmp_path / "repo")


def test_workflow_pins_checkout_and_keeps_non_pr_smoke_unconditional():
    checkout = next(s for s in _job()["steps"] if str(s.get("uses", "")).startswith("actions/checkout"))
    assert checkout["with"]["fetch-depth"] == "0"
    assert checkout["with"]["ref"] == "${{ github.sha }}"
    assert _decide_step()["if"] == "github.event_name == 'pull_request'"
    assert "${{" not in _decide_step()["run"]  # no event metadata shell interpolation
    smoke = next(s for s in _job()["steps"] if s.get("name") == "Run smoke journeys (mock)")
    assert smoke["if"] == "github.event_name != 'pull_request' || steps.lanes.outputs.run != 'false'"
    assert set(_workflow()["on"]) == {"pull_request", "schedule", "workflow_dispatch"}


@pytest.mark.parametrize("checkout_kind", ["head", "merge"])
def test_frontend_change_runs_on_event_head_or_synthetic_merge(repo, checkout_kind):
    head = repo.pr()
    if checkout_kind == "merge":
        repo.merge(head)
    decision = repo.select(head)
    assert decision["run"] is True
    assert decision["base"] == repo.base
    assert decision["head"] == head


def test_queued_run_after_merge_does_not_use_live_base(repo):
    head = repo.pr()
    merge = repo.merge(head)
    repo.git("checkout", "-q", "master")
    repo.git("merge", "--ff-only", merge)
    live_base = repo.commit({DOC: "master advanced again\n"})
    repo.git("update-ref", "refs/remotes/origin/master", live_base)
    repo.git("checkout", "--detach", "-q", merge)
    # Reproduce the old false green with a successful (but empty) comparison.
    assert repo.git("diff", "--name-only", "origin/master...HEAD") == ""
    decision = repo.select(head)
    assert decision["run"] is True
    assert decision["mergeBase"] == repo.base


def test_base_advancing_does_not_expand_docs_only_event_diff(repo):
    head = repo.pr({DOC: "PR docs\n"})
    merge = repo.merge(head)
    repo.git("checkout", "-q", "master")
    live_base = repo.commit({FRONTEND: "later master change\n"})
    repo.git("update-ref", "refs/remotes/origin/master", live_base)
    repo.git("checkout", "--detach", "-q", merge)
    assert repo.select(head)["run"] is False


def test_pr_diff_uses_merge_base_not_two_dot_endpoint_diff(repo):
    head = repo.pr({DOC: "PR docs\n"})
    repo.git("checkout", "-q", "master")
    base = repo.commit({FRONTEND: "base-only change before event\n"})
    repo.merge(head, base)
    decision = repo.select(head, base=base)
    assert decision["run"] is False
    assert decision["mergeBase"] == repo.base


def test_force_pushed_pr_ref_does_not_replace_event_head(repo):
    head = repo.pr()
    merge = repo.merge(head)
    repo.git("checkout", "-qb", "replacement", repo.base)
    replacement = repo.commit({DOC: "replacement only changes docs\n"})
    repo.git("update-ref", "refs/heads/pr", replacement)
    repo.git("checkout", "--detach", "-q", merge)
    decision = repo.select(head)
    assert decision["run"] is True
    assert decision["head"] == head


@pytest.mark.parametrize("changes", [{DOC: "docs only\n"}, {}, {"app/example.py": "pass\n"}])
def test_verified_docs_backend_and_legitimate_empty_diffs_still_skip(repo, changes):
    head = repo.pr(changes)
    assert repo.select(head)["run"] is False


@pytest.mark.parametrize("change_kind", ["delete", "move_out", "move_in", "unicode", "newline"])
def test_renames_deletes_and_unusual_paths_preserve_mapping(repo, change_kind):
    changes = {
        "delete": {FRONTEND: None},
        "move_out": {FRONTEND: None, "docs/widget.ts": "original widget\n"},
        "move_in": {DOC: None, "frontend/components/guide.md": "guide\n"},
        "unicode": {"frontend/components/地图.ts": "change\n"},
        "newline": {"frontend/components/line\nbreak.ts": "change\n"},
    }[change_kind]
    head = repo.pr(changes)
    assert repo.select(head)["run"] is True


@pytest.mark.parametrize("field", ["base", "head", "checkout"])
@pytest.mark.parametrize("value", ["", ZERO_SHA, "master", "$(touch injected)", "-bad", "a" * 40])
def test_invalid_missing_or_unavailable_event_commits_run(repo, field, value):
    head = repo.pr({DOC: "docs only\n"})
    kwargs = {field: value}
    if field == "head":
        head = kwargs.pop("head")
    # Preserve the empty base value in JSON instead of select()'s default.
    if field == "base":
        kwargs = {"payload": {"pull_request": {"base": {"sha": value}, "head": {"sha": head}}}}
    decision = repo.select(head, **kwargs)
    assert decision["run"] is True
    assert "fail closed" in decision["reason"]
    assert not (repo.root / "injected").exists()


@pytest.mark.parametrize("raw_event", ["{", "null", "{}", '{"pull_request": {}}'])
def test_bad_or_missing_event_metadata_runs(repo, raw_event):
    head = repo.pr({DOC: "docs only\n"})
    assert repo.select(head, raw_event=raw_event)["run"] is True


@pytest.mark.parametrize("event_path", ["missing.json", "."])
def test_missing_or_unreadable_event_file_runs(repo, event_path):
    head = repo.pr({DOC: "docs only\n"})
    assert repo.select(head, event_path=repo.root / event_path)["run"] is True


def test_non_commit_event_object_runs(repo):
    head = repo.pr({DOC: "docs only\n"})
    tree = repo.git("rev-parse", f"{head}^{{tree}}")
    assert repo.select(tree)["run"] is True


def test_mismatched_event_checkout_runs_even_for_docs_only_pr(repo):
    head = repo.pr({DOC: "docs only\n"})
    repo.git("checkout", "--detach", "-q", repo.base)
    assert repo.select(head)["run"] is True


def test_merge_from_different_base_cannot_skip(repo):
    head = repo.pr({DOC: "docs only\n"})
    repo.git("checkout", "-q", "master")
    other_base = repo.commit({FRONTEND: "unexpected merged frontend\n"})
    repo.merge(head, other_base)
    assert repo.select(head)["run"] is True


def test_unrelated_histories_run(repo):
    repo.git("checkout", "--orphan", "unrelated")
    head = repo.commit({DOC: "unrelated history\n"})
    assert repo.select(head)["run"] is True


def test_ambiguous_criss_cross_merge_bases_run(repo):
    left = repo.pr({})
    repo.git("checkout", "-qb", "right", repo.base)
    right = repo.commit({DOC: "right side\n"})
    tree = repo.git("rev-parse", f"{repo.base}^{{tree}}")
    base = repo.git("commit-tree", tree, "-p", left, "-p", right, "-m", "left merge")
    head = repo.git("commit-tree", tree, "-p", right, "-p", left, "-m", "right merge")
    repo.git("checkout", "--detach", "-q", head)
    assert len(repo.git("merge-base", "--all", base, head).splitlines()) == 2
    assert repo.select(head, base=base)["run"] is True


def test_failed_diff_cannot_turn_into_empty_skip(repo):
    head = repo.pr({DOC: "docs only\n"})
    tree = repo.git("rev-parse", f"{head}^{{tree}}")
    (repo.root / ".git" / "objects" / tree[:2] / tree[2:]).unlink()
    decision = repo.select(head)
    assert decision["run"] is True
    assert decision["reason"] == "git diff unavailable (fail closed)"


def test_shallow_history_runs(repo):
    head = repo.pr({DOC: "docs only\n"})
    (repo.root / ".git" / "shallow").write_text(f"{head}\n")
    assert repo.select(head)["run"] is True


def test_missing_base_is_fetched_by_exact_sha(repo, tmp_path):
    head = repo.pr({DOC: "docs only\n"})
    repo.git("checkout", "-q", "master")
    # An event base can be newer than the common ancestor and missing locally.
    base = repo.commit({"app/base.py": "base-only\n"})
    remote = tmp_path / "remote.git"
    repo.git("clone", "--bare", str(repo.root), str(remote))
    repo.git("remote", "add", "origin", str(remote))
    repo.git("checkout", "--detach", "-q", head)
    object_path = repo.root / ".git" / "objects" / base[:2] / base[2:]
    object_path.unlink()
    decision = repo.select(head, base=base)
    assert decision["run"] is False
    assert decision["base"] == base
    assert repo.git("cat-file", "-t", base) == "commit"


@pytest.mark.parametrize("mapper", [
    'export function mapChangedFiles() { throw new Error("broken mapper"); }',
    'export function mapChangedFiles() { return {run: "false", reason: "bad type"}; }',
    'export function mapChangedFiles() { return {run: 0, reason: "bad type"}; }',
    'export function mapChangedFiles() { return {run: false}; }',
    'export function mapChangedFiles() { return null; }',
    'this is not valid javascript',
])
def test_mapper_errors_or_invalid_decisions_cannot_skip(repo, mapper):
    head = repo.pr({DOC: "docs only\n"})
    (repo.root / MAPPER).write_text(mapper)
    assert repo.select(head)["run"] is True


def test_missing_selector_module_uses_workflow_fail_closed_fallback(repo):
    head = repo.pr({DOC: "docs only\n"})
    (repo.root / SELECTOR).unlink()
    assert repo.select(head)["run"] is True


@pytest.mark.parametrize("event_name,payload", [
    ("schedule", {}),
    ("workflow_dispatch", {}),
    ("push", {"before": ZERO_SHA, "after": "a" * 40, "created": True}),
    ("push", {"before": "a" * 40, "after": ZERO_SHA, "deleted": True}),
    ("push", {"before": "a" * 40, "after": "b" * 40, "forced": True}),
    ("unknown_event", {}),
])
def test_non_pr_events_always_run_without_using_pr_diff(repo, event_name, payload):
    decision = repo.select("", checkout="", event_name=event_name, payload=payload)
    assert decision["run"] is True
