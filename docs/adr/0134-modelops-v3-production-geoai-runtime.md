# ADR-0134: ModelOps V3 — Production GeoAI Runtime

**Date:** 2026-09-11
**Status:** Accepted
**Branch:** `feat/modelops-v3-geoai-runtime`
**前序:** ADR-0124（Spatial ModelOps & GeoAI Inference Platform V2）

## Context

V2（ADR-0124）落地了 learned-model 的注册/推理/评估平台：provider 契约、
tile 推理引擎、评估指标、复用缓存、agent 工具面。但距离「生产 GeoAI
runtime」仍有明确缺口（V3 基线审计，`.agent-work` 口径）：

1. **P0 缺陷**：`modelops_tools` 引用了不存在的 `app.lib.modelops.engine`
   ——两个推理工具 dispatch 必炸，且测试从未走 ToolRegistry dispatch；
2. torch/onnx adapter 只是词表前向声明；本地子进程通道缺失；
3. 任务面缺 change detection / super-resolution / temporal
   classification / SAR-optical fusion；无 ROI；无矢量化-拓扑修复；
   无 PostGIS 矢量出口；
4. GPU 维度无调度对接（无账本/warm pool/亲和/多卡提示）；
5. registry 无训练指标/评估引用/部署状态（lineage）；评估无边界指标、
   分区指标、逐折报告与漂移检测；
6. foundation 模型（SAM 族）无架构面（prompt 坐标变换/tile 策略）；
7. 推理产物止步于 DataObject——不进地图图层，无样式建议。

## Decisions

1. **P0 先行 + dispatch 级回归测试**：工具 import 修复与「经
   ToolRegistry dispatch 的回归测试」同 commit 落地——tools 层的 import
   错误从此有测试兜底。
2. **DL 运行时 = 探测驱动的诚实接线**（V3 §B）：torch/onnxruntime/
   tensorrt/openvino 经 lazy probe（import 损坏 = `available=False` +
   原因，Windows DLL 失败是常态）；provider 始终注册、load 时 typed
   失败——注册面永不冒充可用，也不因运行时缺席而阻塞主路径。TensorRT/
   OpenVINO 经 onnxruntime 执行提供者接入（以 `get_available_providers`
   为唯一真相，声明了不存在的 EP 会让会话构造失败）。
3. **真实模型包通道**：`onnx-v1`（单文件 .onnx protobuf 计算图，非
   pickle）/`torchscript-v1`（`torch.jit.load` 语义；pickle 的 `.pth`
   仍在黑名单）单文件工件过 `inspect_model_file`（checksum/尺寸/
   protobuf 哨兵）+ `ModelPackageStore` 内容寻址落盘（persist/resolve
   双验 checksum = ADR-0124「注册+load 双验」的落地）。子进程 worker
   **不来自模型包**（数据包仍 data-only）：operator allowlist 登记脚本
   （`MODELOPS_SUBPROCESS_WORKERS`），descriptor.provider_ref =
   `subprocess@<slot>`。
4. **DL 输出契约单点**：`dl_output.map_dl_outputs` 是 runtime 张量 →
   TileOutput 的唯一实现（onnx/torch/subprocess 三处共用）；
   `descriptor.output_transform`（activation/output_scale）进复用指纹
   ——同模型不同激活是不同输出语义。
5. **任务全集扩展**（V3 §C）按既有纪律：封闭词表 + TileOutput 任务判别
   + 引擎路径 + 确定性参考 provider + 种子。变化检测走双栅格
   （`source_uri_b`，网格严格对齐 typed 门，B 内容 sha 进复用键）；
   超分辨率强制 stride==chip（重叠即鬼影）且产物 transform 按 1/s
   缩放（同地理范围，输出坐标正确性有 oracle 测试）。
6. **ROI = 分析窗口**（V3 §D）：tile 网格在 ROI 上重排（planner 纯函数
   复用），读取时平移到绝对坐标，产物以 `window_origin` 平移恢复
   georef；ROI 进复用指纹；单窗口任务（promptable/temporal）typed 拒绝。
7. **矢量化与地理出口**：类栅格 → 多边形（make_valid 拓扑修复 + 可选
   precision snap + preserve_topology 简化 + 面积过滤 + 稳定排序）；
   PostGIS 是**增量通道**（`MODELOPS_POSTGIS_DSN`，前置缺失 honest
   skip 不抛），表名白名单 + `{fail,append}` 封闭词表——没有 replace
   （静默毁表不在授权面）。GeoJSON 文件产物始终兜底。
8. **GPU 调度对接但不动 GeoCompute**（V3 §E，ADR-0124 决策 8 的延伸）：
   复用 GeoCompute 的 nvidia-smi 探测；ModelOps 自持进程内 VRAM 账本
   （预订/typed 耗尽/OOM 反馈收敛 ×0.5 下限 0.25）；确定性模型→卡亲和
   （同模型常驻同卡）；warm pool 以 loaded cache 的 refcount 钉扎实现
   （LRU 驱逐仍受 refcount 门控）——调度语义归 ModelOps，集群归
   GeoCompute，边界不变。
9. **lineage side-car**（V3 §F）：registry 记录保持不可变；训练指标/
   评估引用（data_object + 数据集 refs）/晋升退役以 append-only JSONL
   旁车日志承载（事件词表封闭，payload 大小上限 + secret 扫描），部署
   状态为推导值。评估/漂移 run 在提供 model_id 时自动入 lineage。
10. **评估补空间语义**（V3 §G）：边界 F1（容差足迹匹配；scipy 膨胀不
    扩数组形状——足迹必须直接构造）、逐 spatial-fold 指标（泄漏防护的
    fold 复用为评估切片）、逐分区（region raster）指标；漂移 = baseline
    vs current 的一致性 mIoU + 类别分布 PSI + 置信度 PSI + 分区定位，
    time/sensor 标签随报告与 lineage 分组（标签语义，不做算法假设）。
11. **foundation 面不下载权重**（V3 §H）：SAM 族接入 = prompt 坐标
    变换（仿射**逆**变换；box 角点同变换）+ prompt 锚定 tile 策略
    （4096px 单窗口上限，超出按 prompt 拆窗）+ 产物 georef；真实权重
    经 remote/extension/subprocess 通道。地理 prompt 与等价像素 prompt
    产物逐位一致是验收 oracle。
12. **图层交付是产物语义的一部分**（V3 §I）：推理 outputs → 图层包
    （封闭 raster/vector role 词表）→ 会话数据引用 + alias；样式建议
    确定性（调色板/渐变）且仅为建议（渲染器保留最终裁量）；provenance
    链（model_id/version/checksum/provider/run_id/reuse_key）随图层与
    ref payload（`_modelops` 字段）下发——图层可追溯回模型版本。

## Non-goals

不训练/不托管基础模型权重；不做 AutoML；不重写 GeoCompute scheduler
（复用其探测与集群语义）；不把 Science 算法混入推理平面；PostGIS 不做
跨表拓扑分析（那是矢量平面的事）；不虚称 torch adapter 在 torch 缺席
环境可用（探测 + typed 失败即全部承诺）。

## Consequences

- 新文件：`app/lib/modelops/{backends,foundation,vectorize}.py`、
  `app/services/modelops/{scheduling,lineage,layer_delivery,geo_output,
  package_store}.py`、providers `{onnx_adapter,torch_adapter,
  subprocess_adapter,dl_output,change_reference,superres_reference,
  temporal_class_reference,fusion_reference}.py`；
- 新 env 旋钮（同步 `.env.example` + conftest 钉扎）：
  `MODELOPS_SUBPROCESS_WORKERS`、`MODELOPS_SUBPROCESS_DEADLINE_S`、
  `MODELOPS_POSTGIS_DSN`、`MODELOPS_WARM_POOL`；
- 种子模型 6 → 10（change/superres/temporal-class/fusion）；
- descriptor 新增 `output_transform`（进指纹 ⇒ V2 时代复用条目自然
  失效——部署语义变化，接受）；
- `TileBatch.valid_mask` 获得默认值 None（契约放宽，向后兼容）；
- 测试基线：modelops 套件 157 → 238+（全部本地确定性；无网络/无巨型
  fixture；真实 ONNX 图跑全管线作为「真实 provider 本地跑通」验收）。
