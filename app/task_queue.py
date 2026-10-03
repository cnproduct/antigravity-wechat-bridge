import asyncio
import logging
from collections import defaultdict
from typing import Callable, Coroutine, Any, Optional
from app.config import settings
from app.wechat_handler import wechat_handler

logger = logging.getLogger("antigravity.queue")


class TaskQueue:
    """
    异步并发任务调度器：
    1. 用户级互斥锁 (Per-User Lock)：防止同一用户短时间内快速连续发多条指令造成同一个 Conversation 上下文并发写入竞争。
    2. 全局并发控制 (Global Semaphore)：限制单台云主机同时运行的最大智能体任务数量，保护 CPU/内存/网络不被打崩。
    3. 繁忙排队告知：当并发数达到上限时，主动在微信告知用户排队等待。
    """

    def __init__(self, max_concurrent: Optional[int] = None):
        self._max_concurrent = max_concurrent or settings.max_concurrent_tasks
        self._semaphore = asyncio.Semaphore(self._max_concurrent)
        self._user_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._active_count = 0
        self._queued_count = 0
        self._lock = asyncio.Lock()

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    @property
    def active_tasks(self) -> int:
        return self._active_count

    @property
    def queued_tasks(self) -> int:
        return self._queued_count

    async def submit_task(
        self,
        coro_fn: Callable[..., Coroutine[Any, Any, Any]],
        *args,
        user_id: str,
        **kwargs
    ):
        """
        提交并调度用户任务（按 user_id 串行加锁，受全局并发信号量限制）
        """
        user_lock = self._user_locks[user_id]

        async with user_lock:
            # 检查全局信号量状态，若已打满则向微信通知排队
            async with self._lock:
                self._queued_count += 1
                is_busy = self._semaphore.locked()

            if is_busy:
                logger.info(f"全局通道已满 ({self._max_concurrent})，微信用户 [{user_id}] 任务进入排队中...")
                wechat_handler.send_proactive_text(
                    user_id=user_id,
                    content=(
                        "⏳ 当前上架处理通道繁忙，您的任务已安全进入排队队列。\n"
                        "系统正在有序调度，请稍候..."
                    )
                )

            try:
                async with self._semaphore:
                    async with self._lock:
                        self._queued_count -= 1
                        self._active_count += 1

                    logger.info(f"开始执行微信用户 [{user_id}] 任务 (当前并发活跃数: {self._active_count})")
                    return await coro_fn(*args, user_id=user_id, **kwargs)
            finally:
                async with self._lock:
                    self._active_count = max(0, self._active_count - 1)


task_queue = TaskQueue()
