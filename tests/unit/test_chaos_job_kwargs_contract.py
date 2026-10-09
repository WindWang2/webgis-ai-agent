"""TC-12：nightly chaos 用例构造 job 的参数必须与 DurableJobStore.build 签名一致。

chaos lane 只在 nightly 跑（需要真实 Redis + worker），签名漂移在 PR 上
无人察觉（曾因 ``build() got an unexpected keyword argument 'name'`` 长期红）。
这里在 PR lane 上直接用同一组参数调 build（纯内存构造，不触库）。
"""
from tests.integration.chaos.test_chaos_worker_kill import CHAOS_JOB_KWARGS


def test_chaos_job_kwargs_match_durable_job_store_build():
    from app.services.jobs import DurableJobStore

    job = DurableJobStore.build(**CHAOS_JOB_KWARGS)
    assert job.task_type == "chaos_probe"
    assert job.session_id == "chaos-s1"
