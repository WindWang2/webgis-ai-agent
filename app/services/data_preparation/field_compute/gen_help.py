"""由真实契约生成 ``docs/field-compute-contract.md``（G19 / WP12）。

函数表来自 ``field_compute.functions.function_help()``，预算/版本/错误码来自
``field_compute.contracts``，单位表来自 ``app.lib.gis.measurement.CANONICAL_UNITS``。
新增函数后必须同步升 ``FUNCTION_SET_VERSION``、补边界测试，再运行本脚本。

放在域内而非根 ``scripts/``：仓库 ``.gitignore`` 的 ``/scripts/*`` 只放行白名单
脚本，域内模块随本任务一起入库，不占用共享忽略规则。

用法（worktree 根目录）：
``python app/services/data_preparation/field_compute/gen_help.py``
"""
from __future__ import annotations

import sys
from pathlib import Path

# 直接以文件路径运行时 sys.path[0] 是脚本所在目录，需把 worktree 根加入。
ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
OUT = ROOT / "docs" / "field-compute-contract.md"

from app.lib.gis.measurement import CANONICAL_UNITS  # noqa: E402
from app.services.data_preparation.field_compute import contracts as C  # noqa: E402
from app.services.data_preparation.field_compute.functions import function_help  # noqa: E402
from app.services.data_preparation.field_compute.scope import SELECTION_RECOVERY_HINT  # noqa: E402

CMD = "python app/services/data_preparation/field_compute/gen_help.py"


def build() -> str:
    L = [
        "# 类型化字段计算工作台 —— 函数与契约帮助\n",
        "> **本文件由真实契约生成**：函数表来自 `field_compute.functions.function_help()`，",
        "> 预算/版本/错误码来自 `field_compute.contracts`，单位表来自 "
        "`app.lib.gis.measurement.CANONICAL_UNITS`。",
        f"> 刷新命令（worktree 根目录）：`{CMD}`\n",
        f"- IR 版本：`{C.IR_VERSION}`；函数集版本：`{C.FUNCTION_SET_VERSION}`；"
        f"收据版本：`{C.RECEIPT_VERSION}`\n",
        "\n## 1. 表达式不是代码\n",
        "只允许 `lit / field / unary / binary / call / case` 六类闭合节点。字段名用 "
        "`{中文 字段名}` 原样引用（按字典取值，不做字符串插值）。",
        "无 `import`、文件/网络访问、属性反射、动态代码、系统时钟、随机数；不使用 "
        "`eval` / `exec` / JS `Function` / 拼接 SQL。",
        "未知节点、未知函数、超预算、保留字段、循环/前向列依赖在**计算前**拒绝并返回 "
        "`code / detail / position / hint`。\n",
        "\n## 2. 首批函数集（契约驱动）\n",
        "| 函数 | 族 | 输入 → 输出 | null 行为 | 单位规则 | 资源 | 说明 |",
        "|---|---|---|---|---|---|---|",
    ]
    for f in function_help():
        sig = ", ".join(f["arg_types"]) or "(无参，读行几何)"
        L.append(
            f"| `{f['name']}` | {f['family']} | {sig} → {f['result_type']} | "
            f"{f['null_behavior']} | `{f['unit_rule']}` | {f['resource']} | "
            f"{f['help_zh']} |")
    L += [
        "\n算子词表：`" + "`, `".join(sorted(C.BINARY_OPS)) + "`；一元：`"
        + "`, `".join(sorted(C.UNARY_OPS)) + "`。\n",
        "\n## 3. 类型与缺失\n",
        "- 类型：`number / string / boolean / datetime / null / error`。"
        "**布尔不是整数**；**空串不是 0**；null 不隐式变值。",
        "- 显式转换：`to_number` 空串 → 行级 `EMPTY_STRING_NOT_ZERO`；"
        "`to_integer` 保 Python int 精度。",
        "- 除零 / 溢出 / 非有限结果：`DIVISION_BY_ZERO` / `NUMERIC_OVERFLOW` / "
        "`NON_FINITE`；NaN/Infinity 绝不写为合法 JSON 数字。",
        "- 大整数 > 2^53-1：字面量编译期拒绝 `UNSAFE_INTEGER`；数据中的大整数以文本"
        "写出并在收据披露 `precision_policy=bigint_as_string`，不静默舍入。",
        "- 行政区代码等文本前导零原样保留（`pad_left` 是显式文本操作，不是数值转换）。\n",
        "\n## 4. 单位与科学语义\n",
        "字段名只是提示，**不是可信单位证据**；单位必须由计划的 `field_units` "
        "显式声明（user-declared）。加减两侧同量纲但不同标尺也必须用 "
        "`convert_unit()` 显式换算。乘除生成派生量并保留分子/分母来源；未知组合保留 "
        "`undeclared`，不编造科学资格。\n",
        "| canonical unit | 量纲 | 显示 | 参考倍率 |",
        "|---|---|---|---|",
    ]
    for k, v in sorted(CANONICAL_UNITS.items()):
        L.append(f"| `{k}` | `{v.dimension.value}` | {v.display} | "
                 f"{v.scale_to_reference} |")
    L += [
        "\n几何函数（`area_km2` / `area_m2` / `length_km` / `length_m`）经 "
        "`crs_safety.classify_crs` 门控：地理 CRS 用 `pyproj.Geod` 椭球面积/长度；"
        "局部度量投影用 shapely 平面几何；3857 等尺度失真投影 → `CRS_NOT_METRIC`；"
        "未知 CRS 或 `assumed` → `CRS_UNKNOWN`。几何列被阻断时"
        "**其他不依赖几何的列仍可执行**。\n",
        "\n## 5. 作用范围\n",
        "- `all_rows`：全体行都计算。",
        "- `predicate`：受限闭谓词（`eq/ne/lt/le/gt/ge/is_null/not_null/in`）。",
        f"- `selection`：必须完整、带 `selection_revision`、稳定唯一 ID 字段。"
        f"{SELECTION_RECOVERY_HINT}",
        "- 范围计算**保留全体行**，未选中行按 `unselected_policy`"
        "（默认 `null`，或 `absent`）处理；仅导出选中子集是另一项显式操作 "
        "`subset_export`，默认 false。\n",
        "\n## 6. 错误策略、版本锚与恢复\n",
        "- 错误策略：`null`（默认，错误行结果为 null 并披露计数与 ≤8 条示例）或 "
        "`abort`（遇首个行级错误终止整次，`ABORTED_BY_ROW_ERROR`）。",
        "- 预览是 **sample/estimate**（≤50 行），采样错误率不是全量错误率；"
        "预览不产出公开 ref，不改变原数据。",
        "- 计划指纹绑定：输入内容哈希 + schema 指纹 + scope + 列 IR + 函数集版本 + "
        "单位 + 时区 + 错误策略。",
        "- 预览/执行各自冻结一致快照；发布前二次核对内容/schema，变化即 "
        "`SNAPSHOT_CHANGED` 中止且不发布。预览后源变化 → `STALE_PLAN`，"
        "刷新预览即可继续。",
        "- 取消 → `CANCELLED`；`POST /field-compute/plan/{plan_id}/cancel` "
        "请求服务端协作取消，取消是**可恢复态**，可原计划重提；写入失败 → "
        "`STORE_FAILED` / `WRITE_FAILED`；均不发布半成品。同计划重复提交返回"
        "既有收据（幂等，稳定键不受 ring 截断影响）。",
        "- 持久化如实区分：`session_ref`（会话级，重开会失效）/ "
        "`project_artifact`（项目清单真实落盘，可重开找回）；"
        "项目清单保存失败降级为 `session_ref` 并披露 `PROJECT_SAVE_FAILED`，"
        "绝不谎称已可重开找回。\n",
        "\n## 7. 支持的数据形态与规模\n",
        "- ref-backed GeoJSON FeatureCollection（含 `crs` / `crs_source`）与真正的 "
        "`rows` 非空间表格；UI 按 `data_shape` 如实披露，不宣称未支持的通用能力。",
        f"- 行数上限 `{C.MAX_ROWS}`（超限在启动前 `ROW_LIMIT_EXCEEDED` 拒绝，"
        f"不静默截断）；列 ≤ `{C.MAX_COLUMNS}`；AST 节点 ≤ `{C.MAX_EXPR_NODES}`、"
        f"深度 ≤ `{C.MAX_EXPR_DEPTH}`；每行操作数 ≤ `{C.MAX_ROW_OPS}`；"
        f"字符串输入 ≤ `{C.MAX_STRING_INPUT}`、结果 ≤ `{C.MAX_RESULT_STRING}`。",
        "- 前端属性计算面板**一次编辑一列**；多列链式依赖（≤8 列，声明顺序即"
        "计算顺序，只允许引用之前的列）请用 Pi 对话或 API，不把 UI 单列说成"
        "已支持多列。\n",
        "\n## 8. 再生成\n",
        "函数表由 `FUNCTIONS` 词表驱动；新增函数必须同步提升 "
        "`FUNCTION_SET_VERSION`、补边界测试，并重跑：\n",
        f"```bash\n{CMD}\n```",
    ]
    return "\n".join(L).rstrip() + "\n"


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build(), encoding="utf-8")
    sys.stdout.write(f"refreshed {OUT}\n")


if __name__ == "__main__":
    main()
