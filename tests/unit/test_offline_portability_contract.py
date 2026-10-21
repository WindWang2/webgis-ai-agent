"""跨平台可移植性 contract（ADR-0202 §可移植性）。

不声称"已在某国产 OS 实测"；这里验证的是部署面代码对平台差异的
**可移植性契约**：路径构造、UTF-8 清单读写、LF 稳定序列化、文件锁
原语可用性。全部 synthetic，不依赖真实数据。
"""
import json
from pathlib import Path


def test_path_construction_uses_pathlib_not_slash_concat():
    """目录拼接必须走 pathlib/ os.path —— 字符串 '/' 拼接在 Windows 断裂。"""
    from app.services import offline_preflight as pf

    root = Path("root") / "geodata"
    # _dir_status 接受 str；Path→str 转换跨平台等价
    assert pf._dir_status(str(root)) == "missing"
    # settings 的路径读取点不手工 split("/")
    import inspect
    src = inspect.getsource(pf)
    assert '.split("/")' not in src


def test_manifest_json_roundtrip_utf8(tmp_path):
    """清单读写强制 UTF-8（Windows 默认 GBK 会把中文 detail 写坏）。"""
    from app.services.offline_inventory import generate_asset_manifest

    payload = generate_asset_manifest()
    target = tmp_path / "assets.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    reloaded = json.loads(target.read_text(encoding="utf-8"))
    assert reloaded == payload  # 中文 note/provisioning 往返无损


def test_manifest_output_is_lf_stable(tmp_path):
    """SBOM/manifest 落盘 LF（manage.py 子命令统一 newline="\n"）——
    autocrlf checkout 上两次生成可 diff（#017d1d41 同族教训）。"""
    from app.services.offline_inventory import generate_sbom

    payload = json.dumps(generate_sbom(), ensure_ascii=False, indent=2)
    target = tmp_path / "sbom.json"
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(payload)
    raw = target.read_bytes()
    assert b"\r\n" not in raw


def test_preflight_write_probe_works_on_platform(tmp_path):
    """DATA_DIR/TMP_DIR 可写性探针在本平台真实可执行。"""
    from app.services.offline_preflight import check_data_dirs
    from unittest import mock

    with mock.patch("app.core.config.settings") as fake_settings:
        fake_settings.DATA_DIR = str(tmp_path / "data")
        fake_settings.TMP_DIR = str(tmp_path / "tmp")
        result = check_data_dirs()
    assert result["status"] == "ok"


def test_file_lock_primitive_available():
    """会话/索引面的文件锁原语存在（POSIX fcntl / Windows msvcrt 二选一）。"""
    try:
        import fcntl  # noqa: F401
        available = True
    except ImportError:
        try:
            import msvcrt  # noqa: F401
            available = True
        except ImportError:
            available = False
    assert available, "本平台既无 fcntl 也无 msvcrt——文件锁契约断裂"


def test_no_windows_only_or_posix_only_path_hardcoding():
    """部署面新代码不得硬编码盘符或 POSIX 绝对路径模板。"""
    import inspect

    from app.services import offline_inventory, offline_preflight

    for mod in (offline_inventory, offline_preflight):
        src = inspect.getsource(mod)
        assert "C:\\" not in src and "/usr/local" not in src, mod.__name__


def test_redis_check_detail_redacts_url_password():
    """#1710：check_redis 成功分支只给脱敏投影，口令不进 detail。

    生产 REDIS_URL 形如 ``redis://:${REDIS_PASSWORD}@redis:6379/0``——
    detail 经 console 表格 / --out 落盘 / --json 三处消费，原样输出即
    口令外泄。
    """
    from unittest import mock

    from app.services import offline_preflight as pf

    url = "redis://:s3cr3t-pw@redis:6379/0"
    with mock.patch("app.core.config.settings") as fake_settings:
        fake_settings.USE_REDIS = True
        fake_settings.REDIS_URL = url
        with mock.patch("redis.from_url") as from_url:
            from_url.return_value.ping.return_value = None
            result = pf.check_redis()
    assert result["status"] == "ok"
    assert "s3cr3t-pw" not in result["detail"]
    # host/db 保留可诊断性
    assert "redis://:***@redis:6379/0" in result["detail"]


def test_redis_down_detail_redacts_url_password():
    """#1710：失败分支同样只输出脱敏投影（连接错误也要可诊断）。"""
    from unittest import mock

    from app.services import offline_preflight as pf

    url = "redis://:s3cr3t-pw@redis:6379/0"
    with mock.patch("app.core.config.settings") as fake_settings:
        fake_settings.USE_REDIS = True
        fake_settings.REDIS_URL = url
        with mock.patch("redis.from_url",
                        side_effect=OSError("connection refused")):
            result = pf.check_redis()
    assert result["status"] == "down"
    assert "s3cr3t-pw" not in result["detail"]
    assert "redis://:***@redis:6379/0" in result["detail"]


def test_check_layer_scrubs_url_and_keyvalue_secrets():
    """#1710：_check 层兜底——任意 check 的 detail 内口令一律脱敏。

    连接串（如异常文本内嵌 DATABASE_URL）与键值形态（password=…）都覆盖，
    防其它 check 复犯同类外泄。
    """
    from app.services.offline_preflight import _check

    detail = _check(
        "database", "down",
        "connect failed: postgresql://u:pw123456@db:5432/app",
    )["detail"]
    assert "pw123456" not in detail
    assert "postgresql://u:***@db:5432/app" in detail

    kv = _check("x", "down", "cfg password=abcdef123456")["detail"]
    assert "abcdef123456" not in kv
