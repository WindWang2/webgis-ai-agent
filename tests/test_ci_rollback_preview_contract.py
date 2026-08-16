"""Issue #520：rollback / preview 必须显式导出 WEBGIS_IMAGE。

compose 的 api/celery 用 `image: ${WEBGIS_IMAGE:-webgis-ai-agent:local}` 插值。
修复前：rollback 未导出 → 共享脚本 ci-generate-env-priv.sh 回落到
GITHUB_SHA = 当前坏 HEAD，回滚实际重跑坏镜像；preview 不跑 env 脚本且 checkout
没有递归拉取 vendor/pi 子模块，校验的是本地重建、缺 vendor/pi 的镜像。
本文件守住修复不被拆掉（纯结构断言，pattern 同 tests/test_real_services_ci_wiring.py）。
"""
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "production.yml"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _step(job: str, name: str) -> dict:
    for s in _workflow()["jobs"][job]["steps"]:
        if s.get("name") == name:
            return s
    raise AssertionError(f"{job} 缺少步骤 {name!r}")


def test_rollback_deploy_step_env_exports_webgis_image():
    env = _step("rollback", "Deploy Rollback via SSH")["env"]
    assert "WEBGIS_IMAGE" in env, "rollback Deploy 步骤必须导出 WEBGIS_IMAGE"
    # 回滚镜像（registry pull 或源码 rebuild）统一打 :rollback tag —— compose
    # 必须解析到该 tag，否则脚本回落到 GITHUB_SHA = 坏 HEAD。
    assert env["WEBGIS_IMAGE"].endswith(":rollback"), env["WEBGIS_IMAGE"]


def test_rollback_fallback_printf_writes_webgis_image():
    run = _step("rollback", "Deploy Rollback via SSH")["run"]
    # 回退构建路径检出的 PREV_SHA 可能早于 ci-generate-env-priv.sh —— printf
    # 兜底必须同样写入 WEBGIS_IMAGE，否则 compose 落到 webgis-ai-agent:local。
    assert 'WEBGIS_IMAGE=%s' in run, "printf 兜底必须写 WEBGIS_IMAGE 行"
    assert "$WEBGIS_IMAGE" in run, "printf 兜底必须引用已导出的 WEBGIS_IMAGE"


def test_preview_checkout_has_recursive_submodules():
    with_ = _step("preview", "Checkout Code").get("with", {})
    assert with_.get("submodules") == "recursive", (
        "preview checkout 必须递归拉取 vendor/pi 子模块（Dockerfile.prod COPY vendor/）"
    )


def test_preview_stack_step_writes_webgis_image_into_env_priv():
    run = _step("preview", "Start Preview Stack")["run"]
    assert "WEBGIS_IMAGE" in run, "Start Preview Stack 必须把 WEBGIS_IMAGE 写进 .env.Priv"
    assert "${{ github.sha }}" in run, "预览必须解析到刚 load 的 sha 镜像"
    assert "${{ env.REGISTRY }}" in run and "${{ env.IMAGE_NAME }}" in run


def test_preview_job_lowercases_image_name():
    """build job 把 IMAGE_NAME 小写化后 docker load 的镜像 tag 是全小写；
    preview 若不小写，compose 引用混合大小写 tag 找不到镜像 → 静默 build 兜底。"""
    steps = _workflow()["jobs"]["preview"]["steps"]
    run_text = "\n".join(s.get("run", "") for s in steps if s.get("run"))
    assert "tr '[:upper:]' '[:lower:]'" in run_text, "preview 必须小写化 IMAGE_NAME"


def test_preview_stack_step_has_no_dead_image_tag_env():
    step = _step("preview", "Start Preview Stack")
    assert "IMAGE_TAG" not in step.get("env", {}), "IMAGE_TAG 步骤变量无人消费，应删除"


def test_env_priv_example_exposes_webgis_image_template():
    example = (REPO_ROOT / ".env.Priv.example").read_text(encoding="utf-8")
    assert "WEBGIS_IMAGE=" in example, ".env.Priv.example 必须暴露 WEBGIS_IMAGE 模板行"
