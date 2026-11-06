"""Pure tool-name compatibility and result-classification contracts."""
from app.lib.tool_result_errors import is_suspicious_result as is_suspicious_result

# ─── 规范化工具名称映射表 ─────────────────────────────
# 把遗留工具名规范化为 canonical webgis_* 名称。两个调用点，均刻意保留：
# (1) dispatch 入口 (本模块 dispatch()) — 规范化来自 ChatEngine LLM 输出 / Pi bridge
#     客户端请求的 *实时* tool_call，避免模型或客户端发出遗留名时 registry 找不到工具。
# (2) history replay (history_service_async) — 重放存储的历史 tool_call 前翻译遗留名。
# 见 ADR-0010：架构评审曾提议移除 (1)，但两条实时路径都可能收到遗留名，(1) 并非冗余；
# 由 test_dispatch_normalizes_legacy_tool_names ("Seam B") 锁定。

LEGACY_TOOL_NAME_MAP: dict[str, str] = {
    # 图层管理与样式设置
    "add_layer": "webgis_layer_upsert",
    "set_layer_style": "webgis_layer_upsert",
    "update_layer": "webgis_layer_upsert",
    # remove_layer 不在别名表：#516 其已是 registry 现役工具
    # (app/tools/layer_manager.py，参数 layer_ref)。别名改写会把合法调用
    # 重定向到 webgis_layer_remove (layer_id schema)，导致校验失败——
    # LLM 按目录可见名调用即命中现役工具。
    "delete_layer": "webgis_layer_remove",
    # 视角与导航
    "set_view": "webgis_view_set",
    "move_view": "webgis_view_set",
    # zoom_to_layer 不在别名表：#516 其已是 registry 现役工具
    # (app/tools/map_view.py，参数 layer_ref/padding)。改写为全可选
    # webgis_view_set 会吞掉 layer_ref 参数使缩放命令永不触发。
    # MapSpec 生命周期与检验
    "init_project": "webgis_project_init",
    "get_state": "webgis_state_get",
    "get_map_state": "webgis_state_get",
    "profile_source": "webgis_source_profile",
    "get_source_profile": "webgis_source_profile",
    "set_layout": "webgis_layout_set",
    "validate_spec": "webgis_validate",
    "compile_maplibre": "webgis_compile_maplibre",
    "runtime_validate": "webgis_runtime_validate",
    "checkpoint": "webgis_checkpoint",
    "create_checkpoint": "webgis_checkpoint",
}


def normalize_tool_name(name: str) -> str:
    """规范化遗留工具名为 canonical webgis_* 名称 (供 dispatch 入口与历史重放两处调用，见 ADR-0010)。"""
    return LEGACY_TOOL_NAME_MAP.get(name, name)
