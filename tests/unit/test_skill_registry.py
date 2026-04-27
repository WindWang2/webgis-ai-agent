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
