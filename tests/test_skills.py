"""Skill tools tests — _parse_md_frontmatter and _validate_skill_code"""
import pytest
from app.tools.skills import _parse_md_frontmatter, _validate_skill_code


class TestParseMdFrontmatter:
    def test_valid_frontmatter(self):
        text = "---\nname: test_skill\ndescription: A test skill\n---\n\nSome body content here."
        meta, body = _parse_md_frontmatter(text)
        assert meta["name"] == "test_skill"
        assert meta["description"] == "A test skill"
        assert "Some body content here" in body

    def test_missing_frontmatter(self):
        text = "Just body content, no frontmatter."
        meta, body = _parse_md_frontmatter(text)
        assert meta == {}
        assert body == text

    def test_invalid_yaml(self):
        text = "---\ninvalid: [yaml: broken\n---\nbody"
        meta, body = _parse_md_frontmatter(text)
        assert meta == {}
        assert "body" in body

    def test_non_dict_yaml(self):
        text = "---\njust a string\n---\nbody"
        meta, body = _parse_md_frontmatter(text)
        assert meta == {}
        assert body == text


class TestValidateSkillCode:
    def test_clean_code(self):
        code = "def hello():\n    return 'world'\n"
        errors = _validate_skill_code(code)
        assert errors == []

    def test_blocked_import_os(self):
        code = "import os\nos.system('rm -rf /')\n"
        errors = _validate_skill_code(code)
        assert any("os" in e for e in errors)

    def test_blocked_import_subprocess(self):
        code = "import subprocess\n"
        errors = _validate_skill_code(code)
        assert any("subprocess" in e for e in errors)

    def test_blocked_builtin_eval(self):
        code = "eval('__import__(\"os\").system(\"ls\")')\n"
        errors = _validate_skill_code(code)
        assert any("eval" in e for e in errors)

    def test_blocked_builtin_exec(self):
        code = "exec(open('file').read())\n"
        errors = _validate_skill_code(code)
        assert any("exec" in e for e in errors)

    def test_blocked_attribute_system(self):
        code = "import os\nos.path.system('ls')\n"
        errors = _validate_skill_code(code)
        assert any("os" in e or "system" in e for e in errors)

    def test_syntax_error(self):
        code = "def broken(\n"
        errors = _validate_skill_code(code)
        assert any("Syntax" in e for e in errors)
