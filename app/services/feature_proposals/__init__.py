"""feature_proposals —— 持久要素变更提案与字段/几何冲突审阅（G32）。

模块职责：
- contracts：状态机、上限、typed 异常（词表封闭）
- store：per-session JSON 原子持久 + 分布式锁 + 合并租约
- snapshot：base 快照证据（BlobStore CAS，digest 校验）
- candidate：候选求值（G14 derive_changes 复用，seed=持久 op_seed）
- conflicts：三方冲突分类纯函数（read/write/birth/retire 足迹）
- rebase：显式裁决应用与 rebase（新 revision/digest，旧批准失效）
- bridge：批准绑定复核 + 真实 G14 提交桥 + 恢复判据
- service：REST/工具共用的编排面

公开导出与 feature_edit 同风格：服务面函数直接 import，不在此转发全部符号。
"""
from app.services.feature_proposals.contracts import (  # noqa: F401
    FeatureProposalBlocked,
    FeatureProposalConflict,
    FeatureProposalError,
    FeatureProposalNotFound,
    SnapshotUnavailable,
)

# 注：store 单例**不在包顶层导入** —— store → schemas → 包内 contracts 的
# 初始化链会把包 __init__ 卷进 schema 的 partially-initialized 窗口；
# 消费方直接 `from app.services.feature_proposals.store import proposal_store`。
