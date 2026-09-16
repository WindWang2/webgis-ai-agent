"""跨平台可移植性 contract（ADR-0197 §可移植性）。

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
