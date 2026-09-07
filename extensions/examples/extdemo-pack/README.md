# extdemo-pack — GIS Extension Platform 示例扩展包

本包是扩展平台（ADR-0104）的**活文档 + 集成测试夹具**：既给扩展作者当
样板照抄，也被 `tests/unit/extensions_platform/test_example_pack.py` 端到端
驱动（发现 → 校验 → 激活 → 投影核对 → 派发 → 停用 → 零残留）。

包内全部能力**离线、确定性、无网络调用**。

## 文件清单

| 文件 | 作用 |
| --- | --- |
| `manifest.json` | 唯一声明入口。宿主 fail closed 解析：id 必须 `<namespace>.<name>`、声明节与激活期注册逐项核对、`api_version` 与宿主主版本必须一致。本包声明 2 tools + 1 algorithm + 1 data_provider + 1 cartography item + 1 workflow pack。 |
| `main.py` | `entry_point` 模块，必须提供 `activate(ctx)`。所有 SDK spec（工具/算法/provider/制图/recipe）与 `activate()` 都在这里；`activate` 内注册内容与 manifest 声明不完全一致即激活失败（UNDECLARED_REGISTRATION）。 |
| `tile_catalog.py` | 数据 provider 的 adapter：`GeospatialDataSourceAdapter` ABC 的最小诚实实现（`DemoTileCatalogAdapter`）。镜像核心 `WMSWMTSAdapter` 的栅格语义——`query` 不回答矢量要素、诚实返回空结果；`describe` 读捆绑 `catalog.json`；零网络。 |
| `catalog.json` | provider 的静态目录数据（2 个占位瓦片图层）。放在包内随指纹一起哈希：内容变更 ⇒ 指纹变更 ⇒ 宿主可检测篡改。 |
| `health.py` | 健康检查（`diagnostics_entry: "health:check"`）。`check()` 返回宿主约定的 `{"status": "healthy", "messages": []}`；激活后宿主立即跑一次，unhealthy 即回滚。 |
| `README.md` | 本文件：扩展作者文档输入。 |

## 扩展作者必读的四个坑

1. **声明 ↔ 注册完全一致**。manifest 里声明了的条目可以不注册（警告级
   `DECLARED_BUT_UNREGISTERED`，留给 feature flag 场景），但注册了没声明的
   直接 fail closed（error 级 `UNDECLARED_REGISTRATION` → 激活回滚）。
2. **扩展目录不在 sys.path 上**。entry 模块由宿主以独立模块名按文件路径
   加载；兄弟模块必须像 `main.py` 的 `_load_sibling` 那样显式按路径加载，
   不能 `import tile_catalog`。
3. **命名空间前缀是强制的**。工具/源类型投影为 `<ns>_<name>`，算法为
   `<ns>.<id>`，recipe / 组件 type 为 `<ns>_…`。算法的 `tool_candidates`
   要写**投影后的名字**（本包：`extdemo_polygon_compactness`），且工具
   必须先于算法注册（注册期对已注册工具校验存在性）。
4. **诚实性红线**。扩展工具 `tier ≤ 2`、禁 `destructive`；扩展制图组件
   `runtime_status` 只能 `planned/unavailable`（native 留给有渲染器证据的
   前端实现）；`CapabilityRegistry` 是冻结 seam——不得虚构 capability id，
   recipe 的 `preferred_analysis` 只能引用既有 id。

## 权限与信任

- manifest `permissions: ["network"]` 是**声明**（意图），不是授权。
  provider spec `requires_network=True` 强制 manifest 声明 `network`；
  运行期是否放行由运维 grant 决定（测试里 `grants={"extdemo.pack": {"network"}}`）。
- `trust: "trusted_builtin"` 同样是自荐；实际信任由宿主
  `builtin_ids`/allow/block 策略裁决。

## 本地验证

```bash
python -m pytest tests/unit/extensions_platform/test_example_pack.py -q --no-cov
```

测试覆盖：激活状态与投影名、工具派发数学（bbox 面积）、算法数值 smoke
（`run_authoring_checks`，正方形 → π/4 ≈ 0.7854）、provider 经 data fabric
registry 可解析且离线 describe/health、组件 runtime_status=planned、recipe
通过编译期校验（capability / cartography 引用存在性）、`health` 门、
`deactivate` 后五类 registry 零僵尸。
