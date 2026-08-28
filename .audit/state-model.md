# State Model — 存储清单与一致性（2026-08-27）

## 分层

```
后端权威（desired state）
  磁盘 mapspec.json + revisions/(20) + checkpoints/(20) + 指纹 sidecar
  Redis map_state hash: mapspec / layers / _cartographic_mutation_revision
                         / _cartographic_observation / _cartographic_review / viewport / base_layer
前端投影
  session-cursor（committed spec / pending presentation / pendingRemoved / revision）
  useHudStore.layers（不持久化；reload 靠后端 restore）
  组件乐观 override（componentId→placement；spec 收敛自动清）
  ref-source-resolver cache（refId→GeoJSON，含 FAILED 哨兵）
渲染投影
  composeLiveMapSpec(committed, hud, pending, removed) → MapSpecRuntime diff → MapLibre style
```

## 写入矩阵（谁在什么时候写什么）

| 事件 | 磁盘 spec | Redis mapspec | Redis layers | revision | HUD 行 | pending | MapLibre |
|---|---|---|---|---|---|---|---|
| 用户 toggle/opacity/remove | ✓(经 engine) | ✓ | ✓(upsert/remove 同步；patch 不同步 ST-P3-5) | +1 | 乐观先行 | 写→清 | diff |
| 用户拖组件（pointerup） | ✓ patch_component CAS | ✓ | — | +1 | — | override→清 | chrome 层 |
| agent map_product/layer_upsert | ✓（无 CAS，ST-P2-2 覆盖用户 presentation） | ✓ | ✓ | +1 | syncSpecLayers | — | diff |
| agent show/hide（finalize） | hide durable / show 不 durable（ST-P2-1） | | | +1 | ✓ | ✓ | setLayoutProperty |
| SSE agent mapspec 事件 | — | — | — | set（无单调保护 ST-P3-1） | sync | — | diff |
| reload restore | — | 读 | 读 | 初始化 | 重建 | 清 | diff |
| SSE replay | — | — | — | set | sync | — | diff |

## CAS 语义

- 用户路径：expected_revision 必填 → 不一致 superseded（spec 不动，返回当前真相）→ HTTP 409。
- agent component_update：可选 CAS，superseded 显式回传（user interaction wins）。
- 其余 agent 工具：last-writer-wins。
- 粒度：全 spec 单 revision → 不相交字段并发也互 409（假阳性，靠回灌吸收）。

## 已知一致性缺口（对应 findings）

ST-P1-1（remove 409 复活）、ST-P1-2（用户链无串行 + 全层回灌）、ST-P2-1/2/3、ST-P3-1..5。

## 恢复语义

- reload: cursor 重置 → GET /map-state（含当前 fingerprint 防旧代次观察复活）→ committed 优先级：
  观察态指纹匹配 > turn-start 持久化 layers；presentationFromMapSpec 终覆盖；白名单过滤已删层。
- 相机：仅 mapspec.view.framed===true 恢复（ADR-0057）。
- 跨会话：setMapSpecSessionCursor(sid, 0, token) 同步重置（#736 防 A 会话层在 B 身份下组合）。
