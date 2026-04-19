# -*- coding: utf-8 -*-
"""
核心调度器 - 基于 threading 的轻量定时任务引擎
支持 interval（间隔）和 cron 风格（每天固定时间）两种触发方式
"""
import threading
import logging
from datetime import datetime, timedelta
from typing import Any, Callable, Optional, TypedDict

logger = logging.getLogger("l_scheduler")


class JobStatus(TypedDict):
    """对外暴露的任务状态快照。"""

    name: str
    enabled: bool
    schedule: str
    run_count: int
    error_count: int
    last_run: Optional[str]
    next_run: str
    source: str


class Job:
    """一个定时任务"""

    def __init__(
        self,
        name: str,
        func: Callable,
        interval: Optional[float] = None,       # 秒，间隔触发
        at: Optional[str] = None,               # "HH:MM" 每天固定时间触发
        args: tuple = (),
        kwargs: Optional[dict[str, Any]] = None,
        enabled: bool = True,
        source: str = "python",
    ):
        self.name = name
        self.func = func
        self.interval = interval
        self.at = at                            # "HH:MM"
        self.args = args
        self.kwargs = kwargs or {}
        self.enabled = enabled
        self.source = source

        self.last_run: Optional[datetime] = None
        self.next_run: datetime = self._calc_next_run()
        self.run_count = 0
        self.error_count = 0
        self._running = False
        self._state_lock = threading.Lock()

    def _calc_next_run(self) -> datetime:
        now = datetime.now()
        if self.interval is not None:
            return now + timedelta(seconds=self.interval)
        if self.at is not None:
            h, m = map(int, self.at.split(":"))
            candidate = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if candidate <= now:
                candidate += timedelta(days=1)
            return candidate
        return now

    def _dispatch(self, force: bool = False) -> bool:
        """
        尝试把任务标记为“已派发”。
        - force=False: 受 enabled 与 next_run 约束（调度循环调用）
        - force=True: 仅要求当前未运行（手动触发调用）
        """
        with self._state_lock:
            if self._running:
                return False
            if not force:
                if not self.enabled or datetime.now() < self.next_run:
                    return False
            self._running = True
            self.last_run = datetime.now()
            self.run_count += 1
            return True

    def schedule_text(self) -> str:
        """把调度规则转换为人类可读文本。"""
        if self.interval is not None:
            return f"每 {self.interval:g} 秒"
        if self.at is not None:
            return f"每天 {self.at}"
        return "立即"

    def run(self):
        try:
            logger.info(f"[{self.name}] 开始执行 (第 {self.run_count} 次)")
            self.func(*self.args, **self.kwargs)
            logger.info(f"[{self.name}] 执行完成")
        except Exception as e:
            self.error_count += 1
            logger.error(f"[{self.name}] 执行失败: {e}", exc_info=True)
        finally:
            with self._state_lock:
                self.next_run = self._calc_next_run()
                self._running = False


class Scheduler:
    """
    轻量定时任务调度器

    用法::

        s = Scheduler()

        @s.every(60)
        def sync_files():
            ...

        @s.daily("09:00")
        def morning_report():
            ...

        s.start()   # 后台线程运行
        s.stop()    # 停止
    """

    def __init__(self, tick: float = 1.0):
        """
        :param tick: 调度循环间隔（秒），精度下限
        """
        self.tick = tick
        self._jobs: list[Job] = []
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    # 注册 API
    # ------------------------------------------------------------------

    def add_job(self, job: Job):
        self._jobs.append(job)
        logger.info(f"注册任务: {job.name}  下次执行: {job.next_run.strftime('%Y-%m-%d %H:%M:%S')}")

    def every(self, seconds: float, name: Optional[str] = None, enabled: bool = True):
        """装饰器：每隔 N 秒执行一次"""
        def decorator(func: Callable):
            job_name = name or func.__name__
            self.add_job(Job(job_name, func, interval=seconds, enabled=enabled))
            return func
        return decorator

    def daily(self, at: str, name: Optional[str] = None, enabled: bool = True):
        """装饰器：每天 HH:MM 执行一次"""
        def decorator(func: Callable):
            job_name = name or func.__name__
            self.add_job(Job(job_name, func, at=at, enabled=enabled))
            return func
        return decorator

    def remove_job(self, name: str):
        self._jobs = [j for j in self._jobs if j.name != name]

    def get_job(self, name: str) -> Optional[Job]:
        return next((j for j in self._jobs if j.name == name), None)

    def list_jobs(self) -> list[Job]:
        """返回任务对象快照（只读使用）"""
        return list(self._jobs)

    def set_job_enabled(self, name: str, enabled: bool) -> bool:
        """启用/停用指定任务，返回是否成功"""
        job = self.get_job(name)
        if job is None:
            return False
        job.enabled = enabled
        logger.info("任务状态更新: %s -> enabled=%s", name, enabled)
        return True

    def trigger_job_once(self, name: str) -> bool:
        """立即异步触发一次任务，返回是否成功"""
        job = self.get_job(name)
        if job is None:
            return False
        if not job._dispatch(force=True):
            return False
        t = threading.Thread(target=job.run, name=f"job-manual-{job.name}", daemon=True)
        t.start()
        logger.info("手动触发任务: %s", name)
        return True

    # ------------------------------------------------------------------
    # 运行控制
    # ------------------------------------------------------------------

    def _loop(self):
        logger.info("调度器已启动")
        while not self._stop_event.is_set():
            for job in list(self._jobs):
                if job._dispatch(force=False):
                    t = threading.Thread(target=job.run, name=f"job-{job.name}", daemon=True)
                    t.start()
            self._stop_event.wait(self.tick)
        logger.info("调度器已停止")

    def start(self, block: bool = False):
        """启动调度器。block=True 时阻塞当前线程（适合作为主进程运行）"""
        self._stop_event.clear()
        if block:
            self._loop()
        else:
            self._thread = threading.Thread(target=self._loop, name="l_scheduler", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def status(self) -> list[JobStatus]:
        """返回所有任务的状态快照"""
        result: list[JobStatus] = []
        for j in self._jobs:
            result.append({
                "name": j.name,
                "enabled": j.enabled,
                "schedule": j.schedule_text(),
                "run_count": j.run_count,
                "error_count": j.error_count,
                "last_run": j.last_run.strftime("%Y-%m-%d %H:%M:%S") if j.last_run else None,
                "next_run": j.next_run.strftime("%Y-%m-%d %H:%M:%S"),
                "source": j.source,
            })
        return result
