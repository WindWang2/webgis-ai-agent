"""Export Batch Queue — 批量出图串行队列（V11 W6.5，ADR-0166）。

任务书 W6.5：「串行队列 + 断点续传 + 失败重试，禁止并发（资源纪律）」。

设计（资源纪律优先，不引入新的调度设施）：

- **串行**：``max_concurrency = 1`` 是本服务的构造常量语义（类内 assert）
  —— 导出/浏览器/VLM 属重型资源（§0.4），并发由纪律禁止而非配置；
- **失败重试**：每 job 最多 ``max_retries`` 次（默认 2 = 共 3 次尝试）；
  job 失败（最终）不中断批次（fail-soft），如实记入结果；
- **断点续传**：``resume_state``（{completed_ids: [...]}）跳过已完成 job；
  ``run_batch`` 返回的新状态可直接喂给下一次调用 —— 进程重启/会话恢复后
  从断点继续，不重复已产出的重活；
- **确定性**：批内序 = 输入序；重试计数、状态 JSON 全确定（无随机/时钟）；
- **有界**：单批 job 数上限（默认 200）；状态载荷有界。

本模块不落盘（状态由调用方持久化 —— 与既有 session/工件存储解耦，
测试可注入内存态）。执行体（job callable）由调用方提供（真实导出/编译
或测试桩），队列只负责秩序与记账。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

#: 单批 job 数上限（有界载荷）。
MAX_BATCH_JOBS = 200

#: 每 job 的默认重试次数（不含首次尝试）。
DEFAULT_MAX_RETRIES = 2


@dataclass
class ExportJob:
    """一个出图作业（id 是断点续传的身份标签 —— 必须稳定）。"""

    id: str
    run: Callable[[], Any]

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("export job id 不能为空（断点续传身份）")


@dataclass
class JobResult:
    job_id: str
    status: str            # ok | failed
    attempts: int
    error: str = ""
    output: Any = None
    skipped: bool = False  # 断点续传跳过

    def to_dict(self) -> Dict[str, Any]:
        return {
            "jobId": self.job_id, "status": self.status,
            "attempts": self.attempts, "error": self.error[:500],
            "skipped": self.skipped,
        }


@dataclass
class BatchResult:
    results: List[JobResult] = field(default_factory=list)
    resume_state: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok_count(self) -> int:
        return sum(1 for r in self.results if r.status == "ok" and not r.skipped)

    @property
    def failed(self) -> List[JobResult]:
        return [r for r in self.results if r.status == "failed"]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": len(self.results),
            "ok": self.ok_count,
            "failed": len(self.failed),
            "skipped": sum(1 for r in self.results if r.skipped),
            "results": [r.to_dict() for r in self.results],
            "resumeState": self.resume_state,
        }


class ExportBatchQueue:
    """串行批量出图（资源纪律：并发恒为 1；重试 + 断点续传）。"""

    #: 并发恒为 1 —— 导出属重型资源（§0.4 纪律）；改此值须走 ADR。
    max_concurrency = 1

    def __init__(self, *, max_retries: int = DEFAULT_MAX_RETRIES) -> None:
        assert self.max_concurrency == 1, "导出队列禁止并发（资源纪律）"
        if max_retries < 0:
            raise ValueError("max_retries 不能为负")
        self.max_retries = max_retries

    def run_batch(
        self,
        jobs: Sequence[ExportJob],
        *,
        resume_state: Optional[Dict[str, Any]] = None,
    ) -> BatchResult:
        """串行执行批次；返回结果 + 新状态（可直接作为下次 resume_state）。"""
        if len(jobs) > MAX_BATCH_JOBS:
            raise ValueError(f"批次超过上限 {MAX_BATCH_JOBS}（有界载荷）")
        ids = [j.id for j in jobs]
        if len(set(ids)) != len(ids):
            raise ValueError("job id 必须唯一（断点续传身份）")

        completed: List[str] = list((resume_state or {}).get("completed_ids") or [])
        done_set = set(completed)
        # 状态里的历史 id 允许来自更大批次（本批只看交集）
        result = BatchResult()

        for job in jobs:
            if job.id in done_set:
                result.results.append(JobResult(
                    job_id=job.id, status="ok", attempts=0, skipped=True,
                    output=None,
                ))
                continue
            attempts = 0
            last_error = ""
            output: Any = None
            ok = False
            while attempts <= self.max_retries:
                attempts += 1
                try:
                    output = job.run()
                    ok = True
                    break
                except Exception as exc:  # noqa: BLE001 —— 记错误继续重试
                    last_error = f"{type(exc).__name__}: {exc}"
            if ok:
                completed.append(job.id)
                result.results.append(JobResult(
                    job_id=job.id, status="ok", attempts=attempts, output=output,
                ))
            else:
                # fail-soft：批次继续，失败如实记录（不中断后续 job）
                result.results.append(JobResult(
                    job_id=job.id, status="failed", attempts=attempts,
                    error=last_error,
                ))

        result.resume_state = {
            "completed_ids": completed,
            "total_in_state": len(completed),
        }
        return result


__all__ = [
    "ExportJob",
    "JobResult",
    "BatchResult",
    "ExportBatchQueue",
    "MAX_BATCH_JOBS",
    "DEFAULT_MAX_RETRIES",
]
