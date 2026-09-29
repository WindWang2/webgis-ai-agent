"""代表旅程规格的加载（E15：成都学校 / 行政分级 / 密度热点 / 上传-分析-导出 /
用户中途操作 / provider 故障恢复 —— JSON 冻结、可版本 diff）。

规格 JSON 落在 ``tests/fixtures/lab/scenarios/``（评测资产与既有 replay
语料展开件同域）；app 侧只提供加载与路径解析，不静态 import tests ——
与 replay 视觉裁判的 "tests 资产 + 运行期引用" 同构。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

from app.lib.harness.lab.spec import LabScenario, SpecError

__all__ = ["default_spec_dir", "default_spec_paths", "load_spec", "load_specs"]

_REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_SPEC_DIR = _REPO_ROOT / "tests" / "fixtures" / "lab" / "scenarios"


def default_spec_dir() -> Path:
    return DEFAULT_SPEC_DIR


def default_spec_paths(directory: Path = DEFAULT_SPEC_DIR) -> List[Path]:
    """目录下全部 ``*.json`` 规格（路径排序 → 加载顺序确定）。"""
    if not directory.exists():
        return []
    return sorted(p for p in directory.glob("*.json") if p.is_file())


def load_spec(path: Path) -> LabScenario:
    """单规格加载 + 校验（语法/词表错误即 SpecError，不静默降级）。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SpecError(f"{path}: spec root must be an object")
    spec = LabScenario.from_dict(data)
    spec.validate()
    return spec


def load_specs(paths: List[Path]) -> List[LabScenario]:
    return [load_spec(p) for p in paths]
