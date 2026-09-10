# ModelOps V2 — 04 Test Matrix（含 R1 挑战补充）

平台门：`win32` = 仅在 Windows 本地跑（CI/POSIX 亦可）；`no-win32-assert` = 该用例在 win32 不断言特定行为。

## 1. Compatibility（typed 拒绝矩阵）
| 用例 | 期望 |
|---|---|
| wrong band count | COMPATIBILITY_FAILED/BAND_COUNT |
| wrong band order（RGB 模型喂 BGR 栅格） | BAND_ORDER |
| RGB model vs multispectral raster | MODALITY |
| SAR 缺极化（VV/VH 模型只有 VV） | BAND_ORDER（polarization 语义） |
| wrong dtype（float64 模型 vs uint8 输入，声明 cast 不允许） | DTYPE |
| unsupported resolution | RESOLUTION |
| temporal length mismatch | TEMPORAL_LENGTH |
| invalid CRS（不在 crs_requirements 且无 resampling_policy） | CRS |
| nodata-heavy input（>min_valid_data_ratio） | warning NODATA_HEAVY（不阻断，进 manifest） |
| prompt 模式未声明（box prompt vs point-only provider） | PROMPT_MODE |

## 2. Tiling / Engine
| 用例 | 期望 |
|---|---|
| exact tile 整除 | 无 edge chip；确定性窗口序 |
| tiny raster < chip | 单 chip + pad（clamp read + np.pad） |
| edge tile / odd dimensions | core/read 分离正确；输出只取 core |
| overlap 融合 | 概率空间累加；seam 无伪影（oracle） |
| virtual huge raster（合成 100k×100k metadata） | 只读 metadata；窗口数正确；bytes_read 有界（lazy 证明） |
| mid-batch cancellation | InferenceCancelled；cancel_latency 记录；无 orphan线程 |
| deterministic stitch | 同输入两次运行逐位一致 |
| nodata 接缝 oracle | nodata 权重置零；输出掩膜=输入掩膜 |
| 经典迭代器回归 | chunk.iter_chunk_descriptors / windowed 路径不受影响（M7） |

## 3. GPU / Resource（mock_gpu only；win32 无 RLIMIT 断言 m3）
| 用例 | 期望 |
|---|---|
| no GPU + required=cuda | RESOURCE_UNAVAILABLE（allow_cpu_fallback=false） |
| CPU fallback allowed | device=cpu 成功，manifest 记录降级 |
| insufficient VRAM（估计>预算） | RESOURCE_UNAVAILABLE |
| simulated OOM mid-batch | PROVIDER_OOM → 降批（≤2 次）→ 成功或 typed 失败 |
| batch downshift 记录 | PerfCounters.oom_downshifts/batch_sizes |
| cache eviction（超 max_loaded_models） | LRU 驱逐 refcount==0 条目 |
| two models compete（VRAM 挤占） | 驱逐+重载正确；无死锁 |
| unload failure | 条目移除+poisoned（m2） |
| worker loss（extension） | WORKER_CRASHED → typed ProviderError；不悬挂 |
| cancellation（extension） | 延迟上界=call_timeout（M5，no-win32-assert stderr） |
| concurrent first-load（同 key 并发） | provider.load 只发生一次（single-flight，M4） |

## 4. Security
| 用例 | 期望 |
|---|---|
| malicious archive（.. 成员/绝对路径/反斜杠） | PACKAGE_SECURITY_REJECTED |
| symlink member（zip unix mode） | PACKAGE_SECURITY_REJECTED |
| oversized package / member inflation | PACKAGE_SECURITY_REJECTED |
| unsafe pickle（.pkl 成员 / .npy object array） | PACKAGE_SECURITY_REJECTED（load_weights_array 双门） |
| nested archive 成员 | PACKAGE_SECURITY_REJECTED（m4） |
| remote SSRF（私网/回环/云元数据，无 allowlist） | REMOTE_ENDPOINT_REJECTED |
| remote redirect（302 → 私网） | 逐跳拒绝（follow_redirects=False） |
| secret leakage（descriptor/manifest 带 secret 键） | SECRET_LEAK_GUARD；日志/redaction 无 secret |
| bad checksum（注册/load 双验） | MODEL_CHECKSUM_MISMATCH |
| malformed descriptor（未知字段/坏词表） | DESCRIPTOR_INVALID |
| output bomb（provider 超声明输出） | OUTPUT_BUDGET_EXCEEDED |
| provider_ref 未注册实例 | ProviderError（C1 静态拒绝） |

## 5. Provenance / Reuse
| 用例 | 期望 |
|---|---|
| exact fingerprint reuse | 命中；manifest.reused=true；逐位一致 |
| model version/checksum change | miss |
| preprocessing change（normalization/fill/reproject） | miss |
| threshold/postprocess change | miss |
| provider semantic_version change | miss |
| provider_ref change（同 semantic_version 不同实现） | miss（M3-1） |
| input revision change（DataObject 重发布） | miss |
| owner/project isolation | 跨 owner 不可见不可命中 |
| unseeded provider | 不进 reuse（M3-2） |
| metadata fingerprint 不得入 key（B1 静态断言） | 构造点唯一（compute_input_content_identity） |

## 6. Vertical Slices
| Slice | 验收 |
|---|---|
| A semantic segmentation | COG→推理→blend→分类+置信度 COG→publish（cog_raster）→manifest→reuse 命中→renderable（reader 可重开） |
| B promptable | point/box prompt→capability 门→mask 产物→manifest |
| C remote/extension | allowlist remote（fake server）/extension worker→资源计划→可取消→artifact→评估→manifest |

## 7. Performance（结构化断言，非时序脆断言）
metrics.export() 字段完整性：pixels/s、tiles/s、load latency、warm latency、peak memory、batch sizes、windows、bytes_read、merge_work、cache hit/miss、provider RTT、queue wait、cancel latency。断言字段存在 + 值域合理（>0 或 ==0 语义正确），不断言绝对速度。
