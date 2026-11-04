"""Guard build-stage prerequisites without needing a Docker daemon.

Node 25+ no longer bundles Corepack. Python dependency copies also require
the same interpreter minor and distro in the build and runtime stages.
These are source contracts, not a substitute for building both images.
"""

import re
import tomllib
from pathlib import Path

import pytest
from packaging.specifiers import SpecifierSet

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILES = ("Dockerfile", "Dockerfile.prod")
PNPM_VERSION = "10.15.1"


def _stages(dockerfile_name: str) -> dict[str, tuple[str, str]]:
    stages: dict[str, tuple[str, list[str]]] = {}
    current = None
    for line in (REPO_ROOT / dockerfile_name).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("FROM "):
            _, image, _, current = stripped.split()
            stages[current] = (image, [])
        elif current is not None and stripped and not stripped.startswith("#"):
            stages[current][1].append(stripped.removesuffix("\\").strip())
    return {
        name: (image, " ".join(lines)) for name, (image, lines) in stages.items()
    }


@pytest.mark.parametrize("dockerfile_name", DOCKERFILES)
@pytest.mark.parametrize("stage_name", ("frontend-deps", "frontend-builder"))
def test_frontend_bootstraps_exact_pnpm_without_bundled_corepack(
    dockerfile_name: str, stage_name: str
):
    _, instructions = _stages(dockerfile_name)[stage_name]
    bootstrap = f"RUN npm install --global pnpm@{PNPM_VERSION}"
    assert bootstrap in instructions, (
        f"{dockerfile_name}:{stage_name} must install pinned pnpm explicitly; "
        "Node 25+ does not bundle Corepack"
    )
    assert "corepack " not in instructions
    assert instructions.index(bootstrap) < instructions.index("RUN pnpm ")


@pytest.mark.parametrize("dockerfile_name", DOCKERFILES)
def test_python_stages_match_validated_interpreter(dockerfile_name: str):
    expected = (REPO_ROOT / ".python-version").read_text(encoding="utf-8").strip()
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert expected in SpecifierSet(project["project"]["requires-python"])
    python_stages = {
        name: image for name, (image, _) in _stages(dockerfile_name).items()
        if image.startswith("python:")
    }
    assert {"backend-deps", "runner"} <= python_stages.keys()
    for name, image in python_stages.items():
        assert image.startswith(f"python:{expected}-"), (
            f"{dockerfile_name}:{name} uses {image}, but the validated "
            f"interpreter baseline is Python {expected}"
        )


@pytest.mark.parametrize("dockerfile_name", DOCKERFILES)
def test_python_stages_pin_bookworm_for_runtime_system_libraries(dockerfile_name: str):
    images = {
        image for image, _ in _stages(dockerfile_name).values()
        if image.startswith("python:")
    }
    assert len(images) == 1, "Python stages must share one interpreter/distro digest"
    image = images.pop()
    assert re.fullmatch(r"python:\d+\.\d+-slim-bookworm@sha256:[0-9a-f]{64}", image), (
        f"{dockerfile_name}: libgdal32/libproj25 require the explicit bookworm "
        f"baseline, not the floating distro alias: {image}"
    )


@pytest.mark.parametrize("dockerfile_name", DOCKERFILES)
def test_python_dependency_copy_paths_match_both_stages(dockerfile_name: str):
    stages = _stages(dockerfile_name)
    copies = []
    for destination_stage, (destination_image, instructions) in stages.items():
        for source_stage, source_minor, destination_minor in re.findall(
            r"COPY --from=(\S+) /usr/local/lib/python(\d+\.\d+)/site-packages "
            r"/usr/local/lib/python(\d+\.\d+)/site-packages",
            instructions,
        ):
            source_image = stages[source_stage][0]
            copies.append((source_stage, destination_stage))
            assert source_image.startswith(f"python:{source_minor}-"), (
                f"{dockerfile_name}:{source_stage} cannot provide python{source_minor} "
                f"site-packages from {source_image}"
            )
            assert destination_image.startswith(f"python:{destination_minor}-")
            assert source_minor == destination_minor
    assert copies, f"{dockerfile_name}: no Python dependency copies were checked"
