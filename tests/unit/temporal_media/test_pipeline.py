"""WP05/WP06/WP08/WP11 全链：真实 SVG 编译 → rsvg PNG → APNG/GIF → 独立解码。

故障注入（标签提前一帧 / 全片重复首帧 / 删除变化帧 / 容器损坏 / 时长错）
必须让内容 oracle 变红；不是只比较 JSON 计划。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from app.services.temporal_media.encode import (
    build_encoded_frames, encode_media, write_apng,
)
from app.services.temporal_media.freeze import sha256_bytes, sha256_text
from app.services.temporal_media.manifest import (
    build_manifest, package_evidence,
)
from app.services.temporal_media.render import (
    build_compile_doc, render_digital_frame,
)
from app.services.temporal_media.verify import decode_container, verify_delivery

from .conftest import make_plan, temporal_document


def run_pipeline(plan: Dict[str, Any], tmp: Path, *, fmt: str = "apng",
                 faults: Optional[Any] = None):
    plan = dict(plan)
    if fmt != plan["format"]:
        from .conftest import make_plan as _make_plan  # noqa: F401
        plan["format"] = fmt
    compile_doc, omitted = build_compile_doc(plan)
    frames_dir = tmp / "frames"
    svgs_dir = tmp / "svg"
    media_dir = tmp / "media"
    held = {str(o["id"]): {"start": str(o["start"]), "end": str(o["end"])}
            for o in plan["observations"]}
    digital: List[Dict[str, Any]] = []
    for segment in plan["display"]["segments"]:
        digital.append(render_digital_frame(
            plan, segment, compile_doc=compile_doc, omitted_layers=omitted,
            frames_dir=frames_dir, svgs_dir=svgs_dir, held_windows=held))
    encoded = build_encoded_frames(plan, digital)
    if faults is not None:
        encoded, digital, plan = faults(encoded, digital, plan, frames_dir, svgs_dir)
    media = encode_media(plan, encoded, frames_dir, media_dir)
    # GIF 会折叠相邻相同 P 帧；核验必须使用实际容器帧映射。
    effective = media.get("effective_encoded") or encoded
    return plan, digital, effective, media, frames_dir, svgs_dir


def test_scenario_1_real_apng_decode_and_evidence(media_env, small_plan):
    tmp = media_env["tmp"] / "apng"
    plan, digital, encoded, media, frames_dir, svgs_dir = run_pipeline(small_plan, tmp)
    assert all((frames_dir / d["png_path"]).is_file() for d in digital)
    assert all((svgs_dir / f"{d['digital_frame_id']}.svg").is_file() for d in digital)
    assert media["extension"] == ".apng"
    assert media["encoded_frame_count"] == 5
    decoded = decode_container(Path(media["path"]), "apng")
    assert decoded["n_frames"] == 5
    assert decoded["size"] == [640, 360]
    assert [f["duration_ms"] for f in decoded["frames"]] == [1000] * 5
    verification = verify_delivery(plan, encoded, media, digital,
                                   frames_dir, svgs_dir)
    assert verification["passed"], verification["failures"]
    manifest = build_manifest(plan, encoded, media, verification, digital)
    assert len(manifest["observations"]) == 5
    assert len(manifest["segments"]) == 5
    assert len(manifest["digital_frames"]) >= 5
    assert manifest["container"]["total_duration_ms"] == 5000
    assert manifest["verification"]["passed"] is True
    zip_bytes = package_evidence(manifest, frames_dir, svgs_dir)
    import io
    import zipfile
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        names = archive.namelist()
    assert "manifest.json" in names
    assert sum(n.endswith(".png") for n in names) == len(manifest["digital_frames"])
    assert sum(n.endswith(".svg") for n in names) == len(manifest["digital_frames"])


def test_scenario_9_identical_pictures_are_merged_without_losing_identity(media_env):
    _, _, plan = make_plan(temporal_document([2019, 2020, 2021],
                                             identical_picture=True),
                           width=640, height=360, time_display="none")
    tmp = media_env["tmp"] / "merge"
    plan, digital, encoded, media, frames_dir, svgs_dir = run_pipeline(plan, tmp)
    assert len(set(d["png_sha256"] for d in digital)) == 1
    # 容器可合并相同画面，但 manifest 仍保留三个观测身份与总时长
    assert len(encoded) < len(plan["display"]["segments"])
    assert sum(e["duration_ms"] for e in encoded) == 3000
    manifest_segments = [s["index"] for e in encoded for s_idx in e["segment_indices"]
                         for s in [plan["display"]["segments"][s_idx]]]
    assert sorted(manifest_segments) == [0, 1, 2]
    verification = verify_delivery(plan, encoded, media, digital,
                                   frames_dir, svgs_dir)
    assert verification["passed"], verification["failures"]


def test_scenario_10_dwell_repeat_does_not_increase_observations(media_env):
    _, _, plan = make_plan(width=480, height=270, dwell_ms=1000, fps=4)
    tmp = media_env["tmp"] / "repeat"
    plan, digital, encoded, media, frames_dir, svgs_dir = run_pipeline(plan, tmp)
    assert plan["display"]["observation_count"] == 5
    assert plan["display"]["encoded_frame_count"] == 20
    assert len(encoded) == 20
    decoded = decode_container(Path(media["path"]), "apng")
    assert decoded["n_frames"] == 20
    assert sum(f["duration_ms"] for f in decoded["frames"]) == 5000
    verification = verify_delivery(plan, encoded, media, digital,
                                   frames_dir, svgs_dir)
    assert verification["passed"], verification["failures"]


def test_T2_gif_merges_identical_frames_with_explicit_segment_mapping(media_env):
    """GIF 下相同画面被容器合并后，独立解码/时长/身份映射仍须通过。"""
    _, _, plan = make_plan(temporal_document([2019, 2020, 2021],
                                             identical_picture=True),
                           width=480, height=270, format="gif",
                           time_display="none")
    tmp = media_env["tmp"] / "gif-merge"
    plan, digital, encoded, media, frames_dir, svgs_dir = run_pipeline(
        plan, tmp, fmt="gif")
    decoded = decode_container(Path(media["path"]), "gif")
    assert decoded["n_frames"] == len(encoded)
    # 相同画面已在 build_encoded_frames 跨段合并；GIF writer 不再二次合并。
    assert len(encoded) < len(plan["display"]["segments"])
    assert media["quantization"]["container_merged_frames"] >= 0
    assert sum(int(e["duration_ms"]) for e in encoded) == 3000
    assert sorted({i for e in encoded for i in e["segment_indices"]}) == [0, 1, 2]
    verification = verify_delivery(plan, encoded, media, digital,
                                   frames_dir, svgs_dir)
    assert verification["passed"], verification["failures"]


def test_T2c_gif_collapse_branch_is_really_exercised(media_env):
    """构造 PNG 字节不同、量化后 P 帧相同的相邻帧，真正触发 write_gif 折叠。"""
    from PIL import Image, PngImagePlugin

    from app.services.temporal_media.encode import write_gif

    frames_dir = media_env["tmp"] / "gif-collapse"
    frames_dir.mkdir(parents=True)
    red = Image.new("RGBA", (16, 16), (200, 30, 30, 255))
    blue = Image.new("RGBA", (16, 16), (30, 30, 200, 255))

    def save(name: str, pixels: Image.Image) -> Path:
        info = PngImagePlugin.PngInfo()
        info.add_text("frame", name)  # 元数据不同 → PNG 字节不同、像素可相同
        path = frames_dir / f"{name}.png"
        pixels.save(path, pnginfo=info)
        return path

    p_a = save("a", red)
    save("b", blue)
    p_c = save("c", red)
    assert p_a.read_bytes() != p_c.read_bytes()  # 像素相同但字节不同

    def entry(name: str, index: int) -> Dict[str, Any]:
        return {
            "digital_frame_id": name, "png_path": f"{name}.png",
            "duration_ms": 1000, "segment_indices": [index],
            "segment_durations_ms": {str(index): 1000},
            "observation_ids": [f"f-{2019 + index}"],
            "identities": ["real_observation"], "merged": False,
        }

    out = frames_dir / "collapsed.gif"
    loss, effective = write_gif(out, [entry("a", 0), entry("c", 1)],
                                frames_dir)
    assert loss["container_merged_frames"] == 1
    assert len(effective) == 1
    assert effective[0]["duration_ms"] == 2000
    assert effective[0]["segment_indices"] == [0, 1]
    assert effective[0]["segment_durations_ms"] == {"0": 1000, "1": 1000}
    assert set(effective[0]["observation_ids"]) == {"f-2019", "f-2020"}
    assert effective[0]["merged"] is True
    assert "_source_index" not in effective[0]
    decoded = decode_container(out, "gif")
    assert decoded["n_frames"] == 1
    assert decoded["frames"][0]["duration_ms"] == 2000

    # A/B/A：首尾画面相同但**非相邻**，不得被跨段误合并
    loss2, effective2 = write_gif(
        frames_dir / "aba.gif",
        [entry("a", 0), entry("b", 1), entry("c", 2)], frames_dir)
    assert loss2["container_merged_frames"] == 0
    assert len(effective2) == 3


def test_T2b_gif_with_fps_is_typed_rejected_before_any_work():
    from app.services.temporal_media.contracts import TemporalMediaError
    with pytest.raises(TemporalMediaError) as exc:
        make_plan(width=480, height=270, format="gif", fps=4)
    assert exc.value.code == "GIF_FPS_UNSUPPORTED"


def test_T1b_held_display_window_note_is_the_missing_period_window(media_env):
    """沿用段：内容是源观测、窗口注记是本段缺期，不假装源观测的窗口。"""
    _, _, plan = make_plan(temporal_document([2019, 2021]),
                           width=480, height=270, missing_display="hold")
    tmp = media_env["tmp"] / "held-window"
    plan, digital, encoded, media, frames_dir, svgs_dir = run_pipeline(plan, tmp)
    held = digital[1]
    assert held["kind"] == "held"
    assert held["content_window"]["start"] == plan["observations"][0]["start"]
    assert held["display_window"]["start"] == plan["gaps"][0]["start"]
    assert held["display_window"] != held["content_window"]
    svg = (svgs_dir / f"{held['digital_frame_id']}.svg").read_text(encoding="utf-8")
    assert "窗口 [{} ~ {}）".format(held["display_window"]["start"],
                                   held["display_window"]["end"]) in svg
    verification = verify_delivery(plan, encoded, media, digital,
                                   frames_dir, svgs_dir)
    assert verification["passed"], verification["failures"]


def test_gif_compatibility_output_is_real_and_discloses_loss(media_env):
    _, _, plan = make_plan(width=480, height=270, format="gif", dwell_ms=1000)
    tmp = media_env["tmp"] / "gif"
    plan, digital, encoded, media, frames_dir, svgs_dir = run_pipeline(plan, tmp,
                                                                       fmt="gif")
    assert media["extension"] == ".gif"
    decoded = decode_container(Path(media["path"]), "gif")
    assert decoded["n_frames"] >= 1
    assert media["quantization"]["palette_colors"] == 256
    assert media["quantization"]["duration_unit_ms"] == 10
    verification = verify_delivery(plan, encoded, media, digital,
                                   frames_dir, svgs_dir)
    assert verification["passed"], verification["failures"]
    assert any("gif_global_palette" in d for d in plan["disclosures"])


def test_scenario_5_half_open_boundary_only_one_bucket(media_env):
    doc = temporal_document([2019, 2020])
    # 把一条记录放在 [2019,2020) 的上边界：year=2020 只应进 2020 帧
    doc["sources"]["observations"]["inlineData"]["features"].append({
        "type": "Feature", "id": "boundary-2020",
        "geometry": {"type": "Point", "coordinates": [116.9, 39.9]},
        "properties": {"year": 2020, "label": "边界", "value": 999},
    })
    _, _, plan = make_plan(doc, width=480, height=270)
    tmp = media_env["tmp"] / "boundary"
    plan, digital, encoded, media, frames_dir, svgs_dir = run_pipeline(plan, tmp)
    ids_2019 = [i for layer in digital[0]["layers"] for i in layer["feature_ids"]]
    ids_2020 = [i for layer in digital[1]["layers"] for i in layer["feature_ids"]]
    assert "boundary-2020" not in ids_2019
    assert "boundary-2020" in ids_2020
    verification = verify_delivery(plan, encoded, media, digital,
                                   frames_dir, svgs_dir)
    assert verification["passed"], verification["failures"]


@pytest.mark.parametrize("representation", ["epoch_s", "epoch_ms", "date_string"])
def test_scenario_7_representation_is_not_guessed(media_env, representation):
    _, _, plan = make_plan(temporal_document([2019, 2020, 2021],
                                             representation=representation),
                           width=480, height=270)
    assert plan["temporal_contract"]["time_representation"] == representation
    tmp = media_env["tmp"] / f"repr-{representation}"
    plan, digital, encoded, media, frames_dir, svgs_dir = run_pipeline(plan, tmp)
    assert all(d["feature_count"] >= 1 for d in digital)
    verification = verify_delivery(plan, encoded, media, digital,
                                   frames_dir, svgs_dir)
    assert verification["passed"], verification["failures"]


def test_scenario_6_dst_day_boundaries_use_contract_timezone(media_env):
    # 2024-03-10 为 America/New_York DST 切换日：契约时区日界不得漂移
    doc = temporal_document([2024, 2024, 2024], representation="epoch_s",
                            tz="America/New_York", granularity="day",
                            days=[9, 10, 11])
    _, _, plan = make_plan(doc, width=480, height=270)
    assert plan["temporal_contract"]["timezone"] == "America/New_York"
    starts = [o["start"] for o in plan["observations"]]
    assert len(set(starts)) == 3
    tmp = media_env["tmp"] / "dst"
    plan, digital, encoded, media, frames_dir, svgs_dir = run_pipeline(plan, tmp)
    verification = verify_delivery(plan, encoded, media, digital,
                                   frames_dir, svgs_dir)
    assert verification["passed"], verification["failures"]
    assert all(d["content_window"]["start"] == d["display_window"]["start"]
               for d in digital)


def test_scenario_13_two_temporal_layers_are_both_filtered(media_env):
    _, _, plan = make_plan(temporal_document([2019, 2020, 2021],
                                             second_layer=True),
                           width=640, height=360)
    assert len(plan["temporal_contract"]["time_representation"]) > 0
    tmp = media_env["tmp"] / "two-layers"
    plan, digital, encoded, media, frames_dir, svgs_dir = run_pipeline(plan, tmp)
    for evidence in digital:
        assert len(evidence["layers"]) == 2, evidence["layers"]
        for layer in evidence["layers"]:
            assert layer["feature_ids"], layer
    assert [d["feature_count"] for d in digital] == [2, 2, 2]
    verification = verify_delivery(plan, encoded, media, digital,
                                   frames_dir, svgs_dir)
    assert verification["passed"], verification["failures"]


def test_scenario_23_fault_label_one_frame_ahead_is_detected(media_env):
    _, _, plan = make_plan(width=480, height=270)
    tmp = media_env["tmp"] / "fault-label"
    plan, digital, encoded, media, frames_dir, svgs_dir = run_pipeline(plan, tmp)

    # 注入：把第 2 帧的时间标签改成下一帧（并同步伪造 SVG/证据摘要）
    target = digital[1]
    svg_path = svgs_dir / f"{target['digital_frame_id']}.svg"
    svg = svg_path.read_text(encoding="utf-8")
    tampered = svg.replace(f"时间: {target['time_label']}", "时间: 2021")
    svg_path.write_text(tampered, encoding="utf-8")
    target["time_label"] = "2021"
    target["svg_sha256"] = "sha256:" + sha256_text(tampered)
    verification = verify_delivery(plan, encoded, media, digital,
                                   frames_dir, svgs_dir)
    assert not verification["passed"]
    assert any("label" in f for f in verification["failures"])


def test_scenario_23_fault_all_frames_replaced_by_first_is_detected(media_env):
    _, _, plan = make_plan(width=480, height=270)
    tmp = media_env["tmp"] / "fault-repeat"
    plan, digital, encoded, media, frames_dir, svgs_dir = run_pipeline(plan, tmp)
    first = (frames_dir / digital[0]["png_path"]).read_bytes()
    for evidence in digital[1:]:
        (frames_dir / evidence["png_path"]).write_bytes(first)
    verification = verify_delivery(plan, encoded, media, digital,
                                   frames_dir, svgs_dir)
    assert not verification["passed"]
    assert any("checksum" in f for f in verification["failures"])


def test_scenario_23_fault_deleted_change_frame_is_detected(media_env):
    _, _, plan = make_plan(width=480, height=270)

    def drop_one(encoded, digital, plan_arg, frames_dir, svgs_dir):
        return encoded[:-1], digital, plan_arg

    tmp = media_env["tmp"] / "fault-drop"
    plan, digital, encoded, media, frames_dir, svgs_dir = run_pipeline(
        plan, tmp, faults=drop_one)
    verification = verify_delivery(plan, encoded, media, digital,
                                   frames_dir, svgs_dir)
    assert not verification["passed"]
    assert any(f in verification["failures"] for f in
               ("frame_count", "segment_mapping_complete", "total_duration"))


def test_scenario_18_encoder_exit_zero_but_bad_container_is_detected(media_env):
    _, _, plan = make_plan(width=480, height=270)
    tmp = media_env["tmp"] / "fault-container"
    plan, digital, encoded, media, frames_dir, svgs_dir = run_pipeline(plan, tmp)
    path = Path(media["path"])
    raw = bytearray(path.read_bytes())
    raw[-40] ^= 0xFF
    path.write_bytes(bytes(raw))
    verification = verify_delivery(plan, encoded, media, digital,
                                   frames_dir, svgs_dir)
    assert not verification["passed"]
    assert "container_checksum" in verification["failures"]


def test_scenario_18_wrong_total_duration_is_detected(media_env):
    _, _, plan = make_plan(width=480, height=270)
    tmp = media_env["tmp"] / "fault-duration"
    plan, digital, encoded, media, frames_dir, svgs_dir = run_pipeline(plan, tmp)
    # 重编码一个时长错误但仍是合法 APNG 的容器
    broken = [dict(e, duration_ms=int(e["duration_ms"]) + 500) for e in encoded]
    path = Path(media["path"])
    write_apng(path, broken, frames_dir, loop_count=0)
    media = dict(media)
    media["bytes"] = path.stat().st_size
    media["checksum"] = "sha256:" + sha256_bytes(path.read_bytes())
    verification = verify_delivery(plan, encoded, media, digital,
                                   frames_dir, svgs_dir)
    assert not verification["passed"]
    assert any("duration" in f for f in verification["failures"])
