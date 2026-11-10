# G19-TYPED-FIELD-COMPUTE — PR_HANDOFF

**状态：尚未创建 PR**（终止条件未满足）。本文件在 WP12 完成后回填，禁止预填 PR URL。

## 待填（任务书 §6 / WP12 要求）

| 项 | 内容 |
|---|---|
| PR URL | 待填（真实 `gh pr create` 返回） |
| branch | `zcode/goal-g19-typed-field-compute-20261110T131120Z` |
| worktree | `C:/Users/wangj.KEVIN/projects/webgis-worktrees/goal-g19-typed-field-compute-20261110T131120Z` |
| base SHA / head SHA | 待填（PR 前 fetch 后记录） |
| 函数集与版本 | `g19-fc-1.0` / IR `g19-ir-1` / receipt v1（以最终实现为准） |
| 支持数据形态与规模 | 待填（ref-backed GeoJSON；非空间表格如有支持需写明） |
| 共享文件接线位置 | `app/main.py`、`app/tools/__init__.py`、`frontend/lib/i18n/messages.ts`、`frontend/components/sidebar/data-sources-tab.tsx`（详见 `INTERFACES.md §2`） |
| 对 G12–G21 兼容 | 详见 `INTERFACES.md §5` |
| 已执行验收 | 见 `TEST_MATRIX.md` |
| 独立 review / fix / rereview | 见 `REVIEW.md` |
| 迁移 / 回退 | 新增域 + 纯 additive 接线；回退 = revert 单 PR，无数据迁移 |
| 未测限制 | 待如实填写（含线上 CI 未等待、真实 Pi+stub LLM 全链路是否具备条件等） |
