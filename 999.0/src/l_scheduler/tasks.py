# -*- coding: utf-8 -*-
"""
内置任务定义与任务文件解析。

外部 JSON 任务清单属于不可信输入，必须先解析、校验，再转为内部模型。
"""
from __future__ import annotations

import json
import logging
import locale
import os
import subprocess
import sys
from pathlib import Path
from typing import Literal, NotRequired, TypedDict

logger = logging.getLogger("l_scheduler.tasks")


class TaskConfigError(ValueError):
    """任务配置校验失败。"""


class TaskFileSpec(TypedDict):
    """经过校验后的任务文件定义。"""

    name: str
    path: str
    schedule_type: Literal["interval", "daily"]
    interval_seconds: NotRequired[float]
    daily_at: NotRequired[str]
    enabled: bool
    arguments: NotRequired[list[str]]
    working_directory: NotRequired[str]
    success_return_codes: NotRequired[list[int]]


# ------------------------------------------------------------------
# 任务函数
# ------------------------------------------------------------------

def task_heartbeat() -> None:
    """心跳检测任务。"""
    logger.info("heartbeat OK")


def task_clean_temp() -> None:
    """清理临时文件 - 每天 03:00 执行"""
    import glob
    import shutil

    temp_dir = os.path.join(os.environ.get("TEMP", "C:\\Temp"), "lugwit_*")
    removed = 0
    for path in glob.glob(temp_dir):
        try:
            if os.path.isfile(path):
                os.remove(path)
            elif os.path.isdir(path):
                shutil.rmtree(path)
            removed += 1
        except Exception as e:
            logger.warning(f"清理失败: {path} - {e}")
    logger.info(f"清理临时文件完成，共删除 {removed} 项")


def task_sync_config() -> None:
    """同步配置文件 - 每 10 分钟执行一次"""
    config_root = os.environ.get("L_SCHEDULER_ROOT", "")
    logger.info(f"同步配置 (root={config_root})")
    # TODO: 在此添加实际同步逻辑


def _validate_task_dict(raw: object, index: int) -> TaskFileSpec:
    if not isinstance(raw, dict):
        raise TaskConfigError(f"tasks[{index}] 不是对象")

    name = raw.get("name")
    path = raw.get("path")
    schedule_raw = raw.get("schedule")
    interval = raw.get("interval_seconds")
    daily_at_raw = raw.get("daily_at")
    enabled = raw.get("enabled", True)
    arguments = raw.get("arguments", [])
    working_directory = raw.get("working_directory")
    success_return_codes_raw = raw.get("success_return_codes", [0])

    if not isinstance(name, str) or not name.strip():
        raise TaskConfigError(f"tasks[{index}].name 必须是非空字符串")
    if not isinstance(path, str) or not path.strip():
        raise TaskConfigError(f"tasks[{index}].path 必须是非空字符串")
    if not isinstance(enabled, bool):
        raise TaskConfigError(f"tasks[{index}].enabled 必须是布尔值")
    if not isinstance(arguments, list) or not all(isinstance(x, str) for x in arguments):
        raise TaskConfigError(f"tasks[{index}].arguments 必须是字符串数组")
    if working_directory is not None and not isinstance(working_directory, str):
        raise TaskConfigError(f"tasks[{index}].working_directory 必须是字符串或 null")
    if not isinstance(success_return_codes_raw, list) or not success_return_codes_raw:
        raise TaskConfigError(f"tasks[{index}].success_return_codes 必须是非空整数数组")
    if not all(isinstance(x, int) for x in success_return_codes_raw):
        raise TaskConfigError(f"tasks[{index}].success_return_codes 必须是整数数组")

    file_ext = Path(path).suffix.lower()
    if file_ext not in {".bat", ".exe", ".py"}:
        raise TaskConfigError(f"tasks[{index}].path 仅支持 .bat/.exe/.py: {path}")

    schedule_type: Literal["interval", "daily"]
    interval_seconds: float | None = None
    daily_at: str | None = None

    if schedule_raw is None:
        # 向后兼容旧配置：interval_seconds + daily_at
        if isinstance(interval, (int, float)) and interval > 0:
            schedule_type = "interval"
            interval_seconds = float(interval)
        elif isinstance(daily_at_raw, str) and daily_at_raw.strip():
            schedule_type = "daily"
            daily_at = daily_at_raw.strip()
        else:
            raise TaskConfigError(
                f"tasks[{index}] 缺少 schedule，且未提供有效 interval_seconds/daily_at"
            )
    else:
        if not isinstance(schedule_raw, dict):
            raise TaskConfigError(f"tasks[{index}].schedule 必须是对象")
        schedule_type_raw = schedule_raw.get("type")
        if schedule_type_raw not in {"interval", "daily"}:
            raise TaskConfigError(
                f"tasks[{index}].schedule.type 仅支持 interval/daily"
            )
        schedule_type = schedule_type_raw
        if schedule_type == "interval":
            interval_raw = schedule_raw.get("seconds")
            if not isinstance(interval_raw, (int, float)) or interval_raw <= 0:
                raise TaskConfigError(
                    f"tasks[{index}].schedule.seconds 必须是正数"
                )
            interval_seconds = float(interval_raw)
        else:
            at_raw = schedule_raw.get("at")
            if not isinstance(at_raw, str) or not at_raw.strip():
                raise TaskConfigError(
                    f"tasks[{index}].schedule.at 必须是 HH:MM 字符串"
                )
            daily_at = at_raw.strip()

    spec: TaskFileSpec = {
        "name": name.strip(),
        "path": path.strip(),
        "schedule_type": schedule_type,
        "enabled": enabled,
        "arguments": arguments,
        "success_return_codes": success_return_codes_raw,
    }
    if interval_seconds is not None:
        spec["interval_seconds"] = interval_seconds
    if daily_at is not None:
        spec["daily_at"] = daily_at
    if working_directory:
        spec["working_directory"] = working_directory
    return spec


def load_task_file_specs(config_path: str) -> list[TaskFileSpec]:
    """从 JSON 文件加载并校验任务配置。"""
    config_file = Path(config_path)
    if not config_file.exists():
        raise TaskConfigError(f"任务配置文件不存在: {config_file}")

    try:
        raw_obj = json.loads(config_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TaskConfigError(f"任务配置 JSON 解析失败: {exc}") from exc

    if not isinstance(raw_obj, dict):
        raise TaskConfigError("任务配置根节点必须是对象")
    tasks = raw_obj.get("tasks")
    if not isinstance(tasks, list):
        raise TaskConfigError("任务配置必须包含 tasks 数组")

    parsed: list[TaskFileSpec] = []
    for idx, item in enumerate(tasks):
        parsed.append(_validate_task_dict(item, idx))
    return parsed


def save_task_file_specs(config_path: str, task_specs: list[TaskFileSpec]) -> None:
    """把任务配置写回 JSON 文件（统一写成详细 schedule 结构）。"""
    output_tasks: list[dict[str, object]] = []
    for spec in task_specs:
        task_obj: dict[str, object] = {
            "name": spec["name"],
            "path": spec["path"],
            "enabled": spec["enabled"],
            "arguments": spec.get("arguments", []),
            "success_return_codes": spec.get("success_return_codes", [0]),
            "schedule": {},
        }
        schedule_type = spec["schedule_type"]
        if schedule_type == "interval":
            seconds = spec.get("interval_seconds")
            if seconds is None:
                raise TaskConfigError(f"任务缺少 interval_seconds: {spec['name']}")
            task_obj["schedule"] = {"type": "interval", "seconds": seconds}
        else:
            at = spec.get("daily_at")
            if at is None:
                raise TaskConfigError(f"任务缺少 daily_at: {spec['name']}")
            task_obj["schedule"] = {"type": "daily", "at": at}

        working_directory = spec.get("working_directory")
        if working_directory:
            task_obj["working_directory"] = working_directory
        output_tasks.append(task_obj)

    output_obj = {"tasks": output_tasks}
    config_file = Path(config_path)
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(
        json.dumps(output_obj, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _make_command_runner(spec: TaskFileSpec):
    command_path = spec["path"]
    arguments = spec.get("arguments", [])
    working_directory = spec.get("working_directory")
    success_return_codes = set(spec.get("success_return_codes", [0]))

    def _run() -> None:
        ext = Path(command_path).suffix.lower()
        if ext == ".bat":
            cmd = ["cmd", "/c", command_path, *arguments]
        elif ext == ".py":
            cmd = [sys.executable, command_path, *arguments]
        else:
            cmd = [command_path, *arguments]

        logger.info("开始执行文件任务: %s", " ".join(cmd))
        if os.name == "nt":
            # 在 Windows 上隐藏 cmd/bat 任务的黑窗闪烁。
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0
            result = subprocess.run(
                cmd,
                cwd=working_directory or None,
                capture_output=True,
                text=True,
                encoding=locale.getpreferredencoding(False),
                errors="replace",
                check=False,
                startupinfo=startupinfo,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            result = subprocess.run(
                cmd,
                cwd=working_directory or None,
                capture_output=True,
                text=True,
                encoding=locale.getpreferredencoding(False),
                errors="replace",
                check=False,
            )
        if result.stdout:
            logger.info("任务标准输出[%s]:\n%s", spec["name"], result.stdout.rstrip())
        if result.stderr:
            logger.warning("任务标准错误[%s]:\n%s", spec["name"], result.stderr.rstrip())
        if result.returncode not in success_return_codes:
            raise RuntimeError(
                "文件任务执行失败("
                f"name={spec['name']}, return_code={result.returncode}, "
                f"success_codes={sorted(success_return_codes)})"
            )

    return _run


def register_file_tasks(scheduler, task_specs: list[TaskFileSpec]) -> None:
    """把文件任务挂载到调度器。"""
    from l_scheduler.scheduler import Job

    for spec in task_specs:
        interval_seconds = spec.get("interval_seconds")
        daily_at = spec.get("daily_at")
        scheduler.add_job(
            Job(
                name=spec["name"],
                func=_make_command_runner(spec),
                interval=interval_seconds,
                at=daily_at,
                enabled=spec["enabled"],
                source=spec["path"],
            )
        )


# ------------------------------------------------------------------
# 注册入口
# ------------------------------------------------------------------

def register_all(scheduler, heartbeat_interval_seconds: float = 5.0) -> None:
    """
    把所有任务挂载到调度器。
    在 main.py 里调用一次即可。

    :param scheduler: Scheduler 实例
    """
    from l_scheduler.scheduler import Job

    scheduler.add_job(
        Job(
            "heartbeat",
            task_heartbeat,
            interval=heartbeat_interval_seconds,
        )
    )
    scheduler.add_job(Job("clean_temp",   task_clean_temp, at="03:00"))
    scheduler.add_job(Job("sync_config",  task_sync_config, interval=600))
