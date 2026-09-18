"""#1337（audit ISSUE-011）：运行时 .py 技能的完整性锁与 quarantine。

- skills-lock.json 存在 ``runtime_skills`` 段即启用校验：未登记或哈希
  不符的 .py 一律跳过，永不 exec_module；
- create_new_skill 产出默认进 ``<skills_dir>/quarantine`` 子目录 ——
  未经批准不进入可加载面；
- approve_runtime_skill 把哈希写入锁，之后 load_skills 放行。
"""
import hashlib
import json

import pytest

import app.tools.skills as skills_mod


GOOD_SKILL = (
    "def register(registry):\n"
    "    registry.marker = 'LOADED'\n"
)

EVIL_SKILL = (
    "def register(registry):\n"
    "    registry.marker = 'EVIL'\n"
)


def _write_lock(skills_dir, runtime_skills):
    lock = skills_dir / "skills-lock.json"
    lock.write_text(
        json.dumps({"version": 1, "skills": {},
                    "runtime_skills": runtime_skills}),
        encoding="utf-8",
    )
    return lock


def _sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class _Reg:
    marker = "MISSING"


def test_unlisted_skill_is_quarantined(tmp_path):
    (tmp_path / "rogue.py").write_text(GOOD_SKILL, encoding="utf-8")
    _write_lock(tmp_path, {})

    reg = _Reg()
    skills_mod.load_skills(reg, skills_dir=str(tmp_path))
    assert reg.marker == "MISSING", "未登记的技能不得 exec_module"


def test_listed_skill_loads_and_tampered_skill_blocked(tmp_path):
    skill = tmp_path / "ok.py"
    skill.write_text(GOOD_SKILL, encoding="utf-8")
    _write_lock(tmp_path, {"ok.py": _sha(skill)})

    reg = _Reg()
    skills_mod.load_skills(reg, skills_dir=str(tmp_path))
    assert reg.marker == "LOADED"

    # 篡改已登记文件 → 哈希不符 → 拒绝加载
    reg2 = _Reg()
    skill.write_text(EVIL_SKILL, encoding="utf-8")
    skills_mod.load_skills(reg2, skills_dir=str(tmp_path))
    assert reg2.marker == "MISSING", "哈希不符的技能必须被 quarantine"


def test_no_lock_means_no_enforcement(tmp_path):
    """无锁文件 = 旧部署语义：.py 直接加载（向后兼容）。"""
    (tmp_path / "free.py").write_text(GOOD_SKILL, encoding="utf-8")
    reg = _Reg()
    skills_mod.load_skills(reg, skills_dir=str(tmp_path))
    assert reg.marker == "LOADED"


def test_approve_runtime_skill_round_trip(tmp_path):
    skill = tmp_path / "approved.py"
    skill.write_text(GOOD_SKILL, encoding="utf-8")
    _write_lock(tmp_path, {})

    assert skills_mod.approve_runtime_skill(
        "approved.py", str(skill), skills_dir=str(tmp_path))
    reg = _Reg()
    skills_mod.load_skills(reg, skills_dir=str(tmp_path))
    assert reg.marker == "LOADED", "批准后应可加载"


def test_quarantine_dir_is_not_scanned(tmp_path):
    """quarantine 子目录下的 .py 不参与 load_skills 扫描。"""
    qdir = tmp_path / "quarantine"
    qdir.mkdir()
    (qdir / "sneaky.py").write_text(GOOD_SKILL, encoding="utf-8")
    reg = _Reg()
    skills_mod.load_skills(reg, skills_dir=str(tmp_path))
    assert reg.marker == "MISSING"


@pytest.mark.asyncio
async def test_create_new_skill_writes_to_quarantine(tmp_path, monkeypatch):
    """#1337+#1338：agent 产出默认进 quarantine，不直接进入可加载面。"""
    from app.services.skill_creator import skill_creator

    monkeypatch.setenv("ALLOW_DYNAMIC_SKILLS", "true")
    monkeypatch.setattr(skill_creator, "skills_dir", str(tmp_path))

    code = "def register_skills(registry):\n    registry.marker = 'X'\n"
    result = await skills_mod.create_new_skill("agent_skill", code, "d")

    assert "quarantine" in result
    assert (tmp_path / "quarantine" / "agent_skill.py").exists()
    assert not (tmp_path / "agent_skill.py").exists(), (
        "agent 产出不得直接落在可加载目录"
    )
