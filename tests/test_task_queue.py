import pytest
import asyncio
from app.task_queue import TaskQueue


@pytest.mark.asyncio
async def test_task_queue_concurrency_limit():
    queue = TaskQueue(max_concurrent=2)
    running_parallel = 0
    max_observed_parallel = 0
    lock = asyncio.Lock()

    async def dummy_task(uid: str, **kwargs):
        nonlocal running_parallel, max_observed_parallel
        async with lock:
            running_parallel += 1
            if running_parallel > max_observed_parallel:
                max_observed_parallel = running_parallel
        await asyncio.sleep(0.05)
        async with lock:
            running_parallel -= 1
        return f"done_{uid}"

    # 提交 5 个并发任务
    tasks = [
        queue.submit_task(dummy_task, uid=f"user_{i}", user_id=f"user_{i}")
        for i in range(5)
    ]
    results = await asyncio.gather(*tasks)

    assert len(results) == 5
    assert max_observed_parallel <= 2
    assert queue.active_tasks == 0


@pytest.mark.asyncio
async def test_task_queue_per_user_serialization():
    queue = TaskQueue(max_concurrent=5)
    execution_order = []

    async def step_task(step_id: int, **kwargs):
        await asyncio.sleep(0.02)
        execution_order.append(step_id)

    # 同一个用户连续提交3个任务
    user_id = "single_user"
    tasks = [
        queue.submit_task(step_task, step_id=1, user_id=user_id),
        queue.submit_task(step_task, step_id=2, user_id=user_id),
        queue.submit_task(step_task, step_id=3, user_id=user_id)
    ]
    await asyncio.gather(*tasks)

    # 验证同一用户的任务必须严格按顺序执行，避免会话竞争
    assert execution_order == [1, 2, 3]
