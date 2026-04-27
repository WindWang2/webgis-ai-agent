# Testing Coverage + CI/CD Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix failing tests, add unit + integration test coverage for core modules, and harden the CI pipeline with PR triggers and coverage thresholds.

**Architecture:** TDD approach — write failing tests first, then implement/fix. Coverage measured with `pytest-cov` against a 40% baseline. Integration tests use FastAPI `TestClient` against real route definitions. CI gains `pull_request` trigger and coverage gating.

**Tech Stack:** pytest, pytest-asyncio, pytest-cov, FastAPI TestClient, GitHub Actions

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `requirements.txt` | Modify | Add `pytest-cov` to testing section |
| `pytest.ini` | Modify | Add coverage config |
| `.coveragerc` | Create | Coverage exclusion rules |
| `tests/test_tool_registry.py` | Modify | Remove `@pytest.mark.asyncio` from 4 tests |
| `tests/unit/test_skill_registry.py` | Create | Unit tests for `.md` skill parsing |
| `tests/unit/test_session_data.py` | Create | Unit tests for `SessionDataManager` |
| `tests/integration/__init__.py` | Create | Package marker |
| `tests/integration/test_skill_api.py` | Create | API tests for skill listing |
| `tests/integration/test_session_api.py` | Create | API tests for session map-state |
| `.github/workflows/production.yml` | Modify | Add PR trigger, coverage fail-under |

---

### Task 1: Fix 4 Failing Async Tests + Add Coverage Config

**Files:**
- Modify: `tests/test_tool_registry.py:34,47,59,70`
- Modify: `pytest.ini`
- Create: `.coveragerc`
- Modify: `requirements.txt:41-42`

- [ ] **Step 1: Remove `@pytest.mark.asyncio` decorators from 4 tests**

In `tests/test_tool_registry.py`, remove the decorator from lines 34, 47, 59, 70. With `asyncio_mode = auto` in `pytest.ini`, explicit marks conflict and cause failures.

The file becomes:

```python
"""工具注册框架测试"""
import pytest
import asyncio
from app.tools.registry import ToolRegistry, tool


def test_register_and_list():
    registry = ToolRegistry()

    @tool(registry, name="test_tool", description="A test tool")
    def my_tool(query: str) -> dict:
        return {"result": query}

    assert "test_tool" in registry.list_tools()


def test_get_schemas():
    registry = ToolRegistry()

    @tool(registry, name="geocode", description="Geocode a location",
           param_descriptions={"query": "Location name"})
    def geocode(query: str) -> dict:
        return {"lat": 0, "lon": 0}

    schemas = registry.get_schemas()
    assert len(schemas) == 1
    s = schemas[0]
    assert s["type"] == "function"
    assert s["function"]["name"] == "geocode"
    assert "query" in s["function"]["parameters"]["properties"]
    assert s["function"]["parameters"]["required"] == ["query"]


async def test_dispatch_sync():
    registry = ToolRegistry()

    @tool(registry, name="add", description="Add numbers")
    def add(a: int, b: int) -> int:
        return a + b

    result = await registry.dispatch("add", {"a": 3, "b": 5})
    assert result == 8


async def test_dispatch_async():
    registry = ToolRegistry()

    @tool(registry, name="async_echo", description="Async echo")
    async def async_echo(msg: str) -> str:
        return msg

    result = await registry.dispatch("async_echo", {"msg": "hello"})
    assert result == "hello"


async def test_dispatch_with_json_string():
    registry = ToolRegistry()

    @tool(registry, name="echo", description="Echo")
    def echo(msg: str) -> str:
        return msg

    result = await registry.dispatch("echo", '{"msg": "hello"}')
    assert result == "hello"


async def test_dispatch_unknown_raises():
    registry = ToolRegistry()
    with pytest.raises(KeyError):
        await registry.dispatch("nonexistent", {})


def test_optional_params_not_required():
    registry = ToolRegistry()

    @tool(registry, name="search", description="Search")
    def search(query: str, limit: int = 10) -> list:
        return []

    schemas = registry.get_schemas()
    required = schemas[0]["function"]["parameters"]["required"]
    assert "query" in required
    assert "limit" not in required
```

- [ ] **Step 2: Add `pytest-cov` to requirements.txt**

In `requirements.txt`, replace the testing section (lines 41-42):

```
# Testing
pytest>=7.4.4
pytest-asyncio>=0.23.3
pytest-cov>=5.0.0
```

- [ ] **Step 3: Update `pytest.ini` with coverage config**

Replace `pytest.ini` entirely:

```ini
[pytest]
testpaths = tests
pythonpath = .
asyncio_mode = auto
addopts = --cov=app --cov-report=term-missing --cov-fail-under=40
```

- [ ] **Step 4: Create `.coveragerc`**

```ini
[run]
source = app
omit =
    app/skills/*
    */__pycache__/*
    */migrations/*
    tests/*

[report]
exclude_lines =
    pragma: no cover
    if __name__ == .__main__.
    pass
```

- [ ] **Step 5: Run all tests and verify they pass**

Run: `pip install pytest-cov && pytest tests/test_tool_registry.py -v`
Expected: All 7 tests PASS, no errors

- [ ] **Step 6: Commit**

```bash
git add tests/test_tool_registry.py requirements.txt pytest.ini .coveragerc
git commit -m "fix: remove conflicting asyncio marks, add pytest-cov with 40% threshold"
```

---

### Task 2: Unit Tests — Skill Registry (`app/tools/skills.py`)

**Files:**
- Create: `tests/unit/test_skill_registry.py`
- Reference: `app/tools/skills.py`

- [ ] **Step 1: Write failing tests for skill registry**

Create `tests/unit/test_skill_registry.py`:

```python
"""Unit tests for app/tools/skills.py — .md skill loading and parsing."""
import os
import tempfile
import pytest
from app.tools.skills import (
    _parse_md_frontmatter,
    list_md_skills,
    get_md_skill,
    _load_md_skill,
    load_skills,
    _md_skills,
)
from app.tools.registry import ToolRegistry


@pytest.fixture(autouse=True)
def clear_md_skills():
    """Clear the in-memory _md_skills store before each test."""
    _md_skills.clear()
    yield
    _md_skills.clear()


# --- _parse_md_frontmatter ---

class TestParseMdFrontmatter:
    def test_valid_frontmatter(self):
        text = "---\nname: test_skill\ndescription: A test\n---\nBody content here"
        meta, body = _parse_md_frontmatter(text)
        assert meta == {"name": "test_skill", "description": "A test"}
        assert body.strip() == "Body content here"

    def test_missing_frontmatter(self):
        text = "Just plain text without frontmatter"
        meta, body = _parse_md_frontmatter(text)
        assert meta == {}
        assert body == text

    def test_invalid_yaml(self):
        text = "---\nname: [broken yaml\n---\nSome body"
        meta, body = _parse_md_frontmatter(text)
        assert meta == {}
        assert body == text

    def test_empty_body(self):
        text = "---\nname: empty\n---\n"
        meta, body = _parse_md_frontmatter(text)
        assert meta == {"name": "empty"}
        assert body.strip() == ""

    def test_non_dict_yaml(self):
        """YAML that parses to a string/list should be treated as no frontmatter."""
        text = "---\n- item1\n- item2\n---\nBody"
        meta, body = _parse_md_frontmatter(text)
        assert meta == {}
        assert body == text


# --- _load_md_skill ---

class TestLoadMdSkill:
    def test_loads_valid_file(self, tmp_path):
        md_file = tmp_path / "test_skill.md"
        md_file.write_text("---\nname: my_skill\ndescription: Does stuff\n---\nDo the thing")
        _load_md_skill(str(md_file), "test_skill.md")
        assert "my_skill" in _md_skills
        assert _md_skills["my_skill"]["body"] == "Do the thing"

    def test_skips_file_without_name(self, tmp_path):
        md_file = tmp_path / "no_name.md"
        md_file.write_text("---\ndescription: No name field\n---\nBody")
        _load_md_skill(str(md_file), "no_name.md")
        assert len(_md_skills) == 0


# --- list_md_skills / get_md_skill ---

class TestMdSkillAccessors:
    def test_list_md_skills_empty(self):
        assert list_md_skills() == []

    def test_list_md_skills_returns_list(self):
        _md_skills["alpha"] = {"description": "First", "body": "body1", "filename": "a.md"}
        _md_skills["beta"] = {"description": "Second", "body": "body2", "filename": "b.md"}
        skills = list_md_skills()
        names = [s["name"] for s in skills]
        assert "alpha" in names
        assert "beta" in names

    def test_get_md_skill_found(self):
        _md_skills["urban"] = {"description": "Urban planning", "body": "plan cities", "filename": "u.md"}
        skill = get_md_skill("urban")
        assert skill["body"] == "plan cities"

    def test_get_md_skill_not_found(self):
        assert get_md_skill("nonexistent") is None


# --- load_skills (integration with filesystem) ---

class TestLoadSkills:
    def test_loads_both_py_and_md(self, tmp_path):
        # Create a .md skill
        md = tmp_path / "plan.md"
        md.write_text("---\nname: plan_skill\ndescription: Planning\n---\nPlan things")

        # Create a minimal .py skill (no register_skills — just shouldn't crash)
        py = tmp_path / "dummy.py"
        py.write_text("# empty skill\n")

        registry = ToolRegistry()
        load_skills(registry, skills_dir=str(tmp_path))

        assert "plan_skill" in _md_skills

    def test_handles_missing_dir(self, tmp_path):
        registry = ToolRegistry()
        nonexistent = str(tmp_path / "no_such_dir")
        load_skills(registry, skills_dir=nonexistent)
        # Should not raise, and creates the directory
        assert os.path.isdir(nonexistent)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_skill_registry.py -v`
Expected: All tests FAIL (ImportError — tests import from `app.tools.skills` which depends on `app.tools.registry`)

Actually, since `app.tools.skills` already exists, these should pass or fail based on logic. Run to see.

- [ ] **Step 3: Run tests and verify they pass**

Run: `pytest tests/unit/test_skill_registry.py -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_skill_registry.py
git commit -m "test: add unit tests for .md skill registry parsing"
```

---

### Task 3: Unit Tests — Session Data Manager (`app/services/session_data.py`)

**Files:**
- Create: `tests/unit/test_session_data.py`
- Reference: `app/services/session_data.py` (`SessionDataManager` class)

- [ ] **Step 1: Write failing tests for SessionDataManager**

Create `tests/unit/test_session_data.py`:

```python
"""Unit tests for app/services/session_data.py — SessionDataManager (in-memory)."""
import pytest
from app.services.session_data import SessionDataManager


@pytest.fixture
def mgr():
    """Fresh SessionDataManager with small capacity for eviction tests."""
    return SessionDataManager(capacity=5)


class TestStoreAndGet:
    def test_store_returns_ref_id(self, mgr):
        ref = mgr.store("s1", {"geojson": "..."}, prefix="layer")
        assert ref.startswith("ref:layer-")

    def test_store_and_get_roundtrip(self, mgr):
        ref = mgr.store("s1", {"type": "FeatureCollection"})
        result = mgr.get("s1", ref)
        assert result == {"type": "FeatureCollection"}

    def test_get_unknown_session_returns_none(self, mgr):
        assert mgr.get("missing", "ref:layer-abc") is None

    def test_get_unknown_ref_returns_none(self, mgr):
        mgr.store("s1", "data")
        assert mgr.get("s1", "ref:layer-nonexistent") is None


class TestAlias:
    def test_set_alias_and_get_by_alias(self, mgr):
        ref = mgr.store("s1", {"data": 1})
        mgr.set_alias("s1", ref, "my_layer")
        result = mgr.get("s1", "my_layer")
        assert result == {"data": 1}

    def test_get_by_original_ref_still_works(self, mgr):
        ref = mgr.store("s1", {"data": 1})
        mgr.set_alias("s1", ref, "alias1")
        # Both ref and alias should resolve
        assert mgr.get("s1", ref) == {"data": 1}
        assert mgr.get("s1", "alias1") == {"data": 1}


class TestListRefs:
    def test_list_refs_shows_aliases(self, mgr):
        ref = mgr.store("s1", "data")
        mgr.set_alias("s1", ref, "layer_a")
        refs = mgr.list_refs("s1")
        assert ref in refs
        assert refs[ref] == "layer_a"

    def test_list_refs_empty_for_unknown_session(self, mgr):
        assert mgr.list_refs("missing") == {}


class TestLRUEviction:
    def test_evicts_oldest_at_capacity(self, mgr):
        refs = []
        for i in range(6):  # capacity is 5
            ref = mgr.store("s1", f"data_{i}")
            refs.append(ref)

        # First ref should have been evicted
        assert mgr.get("s1", refs[0]) is None
        # Latest ref should still be there
        assert mgr.get("s1", refs[5]) == "data_5"

    def test_eviction_removes_alias(self, mgr):
        refs = []
        for i in range(6):
            ref = mgr.store("s1", f"data_{i}")
            mgr.set_alias("s1", ref, f"alias_{i}")
            refs.append(ref)

        # Evicted item's alias should also be gone
        result = mgr.get("s1", "alias_0")
        assert result is None


class TestMapState:
    def test_set_and_get_map_state(self, mgr):
        mgr.set_map_state("s1", "base_layer", "dark")
        mgr.set_map_state("s1", "zoom", 12)
        state = mgr.get_map_state("s1")
        assert state == {"base_layer": "dark", "zoom": 12}

    def test_get_map_state_empty(self, mgr):
        assert mgr.get_map_state("missing") == {}


class TestEventLog:
    def test_append_and_get_events(self, mgr):
        mgr.append_event("s1", "layer_added", {"id": "l1"})
        mgr.append_event("s1", "query_sent", {"text": "hello"})
        log = mgr.get_event_log("s1")
        assert len(log) == 2
        assert log[0]["event"] == "layer_added"
        assert log[1]["data"] == {"text": "hello"}

    def test_event_log_maxlen_cap(self, mgr):
        for i in range(30):
            mgr.append_event("s1", f"event_{i}", {})
        log = mgr.get_event_log("s1")
        assert len(log) == 20  # deque maxlen=20

    def test_get_event_log_empty(self, mgr):
        assert mgr.get_event_log("missing") == []


class TestClearSession:
    def test_clear_session_removes_everything(self, mgr):
        mgr.store("s1", "data")
        mgr.set_map_state("s1", "key", "val")
        mgr.append_event("s1", "ev", {})
        mgr.clear_session("s1")
        assert mgr.get("s1", "anything") is None
        assert mgr.get_map_state("s1") == {}
        assert mgr.get_event_log("s1") == []
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `pytest tests/unit/test_session_data.py -v`
Expected: All tests PASS (SessionDataManager is pure in-memory, no external deps)

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_session_data.py
git commit -m "test: add unit tests for SessionDataManager (store, alias, LRU, events)"
```

---

### Task 4: Integration Tests — Skill and Session API Endpoints

**Files:**
- Create: `tests/integration/__init__.py`
- Create: `tests/integration/test_skill_api.py`
- Create: `tests/integration/test_session_api.py`

- [ ] **Step 1: Create `tests/integration/__init__.py`**

Empty file — just a package marker:

```python
```

- [ ] **Step 2: Write integration tests for skill API**

Create `tests/integration/test_skill_api.py`:

```python
"""Integration tests for skill API endpoints using FastAPI TestClient."""
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from app.tools.skills import _md_skills


@pytest.fixture(autouse=True)
def clear_skills():
    _md_skills.clear()
    yield
    _md_skills.clear()


@pytest.fixture
def client():
    """Create TestClient with a minimal app that includes only the chat router."""
    from fastapi import FastAPI
    from app.api.routes.chat import router as chat_router

    app = FastAPI()
    app.include_router(chat_router, prefix="/api/v1")
    return TestClient(app)


class TestSkillListAPI:
    def test_list_skills_empty(self, client):
        resp = client.get("/api/v1/chat/skills")
        assert resp.status_code == 200
        assert resp.json() == {"skills": []}

    def test_list_skills_returns_loaded_skills(self, client):
        _md_skills["urban_planning"] = {
            "description": "城市规划设计",
            "body": "分析城市布局...",
            "filename": "urban_planning.md",
        }
        resp = client.get("/api/v1/chat/skills")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["skills"]) == 1
        assert data["skills"][0]["name"] == "urban_planning"
```

- [ ] **Step 3: Write integration tests for session API**

Create `tests/integration/test_session_api.py`:

```python
"""Integration tests for session map-state API endpoint."""
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create TestClient with a minimal app that includes only the chat router."""
    from fastapi import FastAPI
    from app.api.routes.chat import router as chat_router

    app = FastAPI()
    app.include_router(chat_router, prefix="/api/v1")
    return TestClient(app)


class TestSessionMapStateAPI:
    @patch("app.api.routes.chat.session_data_manager")
    def test_get_map_state_returns_state(self, mock_sdm, client):
        mock_sdm.get_map_state.return_value = {
            "base_layer": "dark",
            "layers": [{"id": "l1", "type": "geojson"}],
        }
        resp = client.get("/api/v1/chat/sessions/sess-123/map-state")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "sess-123"
        assert data["map_state"]["base_layer"] == "dark"

    @patch("app.api.routes.chat.session_data_manager")
    def test_get_map_state_empty(self, mock_sdm, client):
        mock_sdm.get_map_state.return_value = {}
        resp = client.get("/api/v1/chat/sessions/sess-404/map-state")
        assert resp.status_code == 200
        assert resp.json()["map_state"] == {}
```

- [ ] **Step 4: Run integration tests**

Run: `pytest tests/integration/ -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add tests/integration/__init__.py tests/integration/test_skill_api.py tests/integration/test_session_api.py
git commit -m "test: add integration tests for skill listing and session map-state APIs"
```

---

### Task 5: CI Pipeline Hardening

**Files:**
- Modify: `.github/workflows/production.yml`

- [ ] **Step 1: Add `pull_request` trigger and coverage threshold**

In `.github/workflows/production.yml`, make two changes:

**Change 1 — Add `pull_request` trigger** (after line 13, add):

Replace the `on:` block (lines 9-17) with:

```yaml
on:
  push:
    branches:
      - main
      - 'release/**'
  pull_request:
    branches:
      - '**'
  schedule:
    - cron: '0 2 * * *'
  workflow_dispatch:
```

**Change 2 — Add coverage fail-under to test step** (line 100):

Replace the "Run Tests with Coverage" step (line 99-100) with:

```yaml
      - name: Run Tests with Coverage
        run: pytest --cov=app --cov-report=xml --cov-report=term-missing --cov-fail-under=40 -v
```

Also add artifact upload after the existing codecov step (after line 106):

```yaml
      - name: Upload Coverage XML
        uses: actions/upload-artifact@v4
        with:
          name: coverage-report
          path: coverage.xml
          retention-days: 7
```

- [ ] **Step 2: Verify YAML is valid**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/production.yml'))"`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/production.yml
git commit -m "ci: add PR trigger, coverage fail-under=40, upload coverage artifact"
```

---

### Task 6: Full Test Suite Validation

**Files:** None (validation only)

- [ ] **Step 1: Install pytest-cov**

Run: `pip install pytest-cov`

- [ ] **Step 2: Run full test suite with coverage**

Run: `pytest --cov=app --cov-report=term-missing --cov-fail-under=40 -v`
Expected: All tests pass, coverage >= 40%

- [ ] **Step 3: Verify integration tests pass**

Run: `pytest tests/integration/ -v`
Expected: All integration tests pass

- [ ] **Step 4: Fix any remaining issues and commit**

If any tests fail, fix them and commit with an appropriate message. If all pass, this step is a no-op.
