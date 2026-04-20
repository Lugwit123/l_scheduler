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
import datetime
import re
import time
from pathlib import Path
from typing import Literal, NotRequired, TypedDict, cast

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
    # 如果任务会自己生成独立日志（尤其是 .bat），可在这里显式指定日志文件路径，
    # UI 切换到该任务时会直接打开该日志。
    # 同时执行任务时会把该路径写入外部日志指针文件，并可选注入到环境变量（默认 LOG_FILE）。
    external_log_file: NotRequired[str]
    external_log_env_var: NotRequired[str]


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

    # ty 对 isinstance(raw, dict) 后的 raw.get 推断在部分版本会异常；这里显式 cast。
    raw_dict = cast(dict[str, object], raw)

    name = raw_dict.get("name")
    path = raw_dict.get("path")
    schedule_raw = raw_dict.get("schedule")
    interval = raw_dict.get("interval_seconds")
    daily_at_raw = raw_dict.get("daily_at")
    enabled = raw_dict.get("enabled", True)
    arguments = raw_dict.get("arguments", [])
    working_directory = raw_dict.get("working_directory")
    success_return_codes_raw = raw_dict.get("success_return_codes", [0])
    external_log_file = raw_dict.get("external_log_file")
    external_log_env_var = raw_dict.get("external_log_env_var")

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
    if external_log_file is not None and not isinstance(external_log_file, str):
        raise TaskConfigError(f"tasks[{index}].external_log_file 必须是字符串或 null")
    if external_log_env_var is not None and not isinstance(external_log_env_var, str):
        raise TaskConfigError(f"tasks[{index}].external_log_env_var 必须是字符串或 null")
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
        schedule_dict = cast(dict[str, object], schedule_raw)
        schedule_type_raw = schedule_dict.get("type")
        if schedule_type_raw not in {"interval", "daily"}:
            raise TaskConfigError(
                f"tasks[{index}].schedule.type 仅支持 interval/daily"
            )
        schedule_type = cast(Literal["interval", "daily"], schedule_type_raw)
        if schedule_type == "interval":
            interval_raw = schedule_dict.get("seconds")
            if not isinstance(interval_raw, (int, float)) or interval_raw <= 0:
                raise TaskConfigError(
                    f"tasks[{index}].schedule.seconds 必须是正数"
                )
            interval_seconds = float(interval_raw)
        else:
            at_raw = schedule_dict.get("at")
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
        "arguments": cast(list[str], arguments),
        "success_return_codes": cast(list[int], success_return_codes_raw),
    }
    if interval_seconds is not None:
        spec["interval_seconds"] = interval_seconds
    if daily_at is not None:
        spec["daily_at"] = daily_at
    if working_directory:
        spec["working_directory"] = working_directory
    if external_log_file and external_log_file.strip():
        spec["external_log_file"] = external_log_file.strip()
    if external_log_env_var and external_log_env_var.strip():
        spec["external_log_env_var"] = external_log_env_var.strip()
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
        external_log_file = spec.get("external_log_file")
        if external_log_file:
            task_obj["external_log_file"] = external_log_file
        external_log_env_var = spec.get("external_log_env_var")
        if external_log_env_var:
            task_obj["external_log_env_var"] = external_log_env_var
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
        started_at = time.time()
        log_root = os.environ.get("L_SCHEDULER_LOG_FILE", "logs/l_scheduler.log")

        # 如果配置里明确给了外部日志文件，则提前写指针，保证 UI 切换下拉框即可看到。
        external_log_file = spec.get("external_log_file")
        if external_log_file:
            expanded = os.path.expandvars(external_log_file)
            _record_external_log_path(
                task_name=spec["name"],
                log_root=log_root,
                target=expanded,
            )
        ext = Path(command_path).suffix.lower()
        if ext == ".bat":
            cmd = ["cmd", "/c", command_path, *arguments]
        elif ext == ".py":
            cmd = [sys.executable, command_path, *arguments]
        else:
            cmd = [command_path, *arguments]

        logger.info("开始执行文件任务: %s", " ".join(cmd))
        _append_task_log(
            task_name=spec["name"],
            log_root=log_root,
            message=f"开始执行: {' '.join(cmd)}",
        )
        env = os.environ.copy()
        # 给 bat 注入 LOG_FILE（或指定变量名），实现 bat 内部 %LOG_FILE% 与 UI 指向一致。
        if external_log_file:
            env_var = spec.get("external_log_env_var") or "LOG_FILE"
            env[env_var] = os.path.expandvars(external_log_file)
        if os.name == "nt":
            # 在 Windows 上隐藏 cmd/bat 任务的黑窗闪烁。
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0
            result = subprocess.run(
                cmd,
                cwd=working_directory or None,
                env=env,
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
                env=env,
                capture_output=True,
                text=True,
                encoding=locale.getpreferredencoding(False),
                errors="replace",
                check=False,
            )
        finished_at = time.time()
        if result.stdout:
            logger.info("任务标准输出[%s]:\n%s", spec["name"], result.stdout.rstrip())
            _append_task_log(
                task_name=spec["name"],
                log_root=log_root,
                message=f"STDOUT:\n{result.stdout.rstrip()}",
            )
        if result.stderr:
            logger.warning("任务标准错误[%s]:\n%s", spec["name"], result.stderr.rstrip())
            _append_task_log(
                task_name=spec["name"],
                log_root=log_root,
                message=f"STDERR:\n{result.stderr.rstrip()}",
            )
        _append_task_log(
            task_name=spec["name"],
            log_root=log_root,
            message=f"退出码: {result.returncode}",
        )
        _append_task_log(
            task_name=spec["name"],
            log_root=log_root,
            message=f"耗时: {finished_at - started_at:.3f} 秒",
        )

        # 如果任务本身会生成独立日志文件，尽量从 stdout/stderr 中解析出路径，供 UI 直接查看。
        _maybe_record_external_log_path(
            task_name=spec["name"],
            log_root=log_root,
            text=f"{result.stdout or ''}\n{result.stderr or ''}",
        )
        # bat 往往把日志写到磁盘但不会在 stdout/stderr 打印路径；这里做一次文件系统兜底探测。
        _maybe_record_external_log_path_from_fs(
            task_name=spec["name"],
            log_root=log_root,
            working_directory=working_directory,
            command_path=command_path,
            started_at=started_at,
            finished_at=finished_at,
        )
        if result.returncode not in success_return_codes:
            raise RuntimeError(
                "文件任务执行失败("
                f"name={spec['name']}, return_code={result.returncode}, "
                f"success_codes={sorted(success_return_codes)})"
            )

    return _run


def _safe_file_stem(name: str) -> str:
    return "".join(ch if (ch.isalnum() or ch in ("-", "_", ".")) else "_" for ch in name)


def _append_task_log(*, task_name: str, log_root: str, message: str) -> None:
    """把每次任务运行的输出落盘到 logs/tasks/<task>.log，便于 UI 单独查看。"""
    try:
        root = Path(log_root)
        log_dir = (root.parent if root.suffix else root) / "tasks"
        log_dir.mkdir(parents=True, exist_ok=True)
        p = log_dir / f"{_safe_file_stem(task_name)}.log"
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with p.open("a", encoding="utf-8", newline="\n") as f:
            f.write(f"[{ts}] {message}\n\n")
    except Exception:
        # 任务执行不可因写日志失败而中断
        logger.debug("写入任务专属日志失败", exc_info=True)


def _task_log_dir_from_root(log_root: str) -> Path:
    root = Path(log_root)
    return (root.parent if root.suffix else root) / "tasks"


def _external_log_pointer_path(task_name: str, log_root: str) -> Path:
    log_dir = _task_log_dir_from_root(log_root)
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / f"{_safe_file_stem(task_name)}.external_log_path.txt"


def _record_external_log_path(*, task_name: str, log_root: str, target: str) -> None:
    """把外部日志路径写入指针文件，供 UI 直接打开。"""
    try:
        if not target:
            return
        p = Path(target)
        ptr = _external_log_pointer_path(task_name, log_root)
        # 不强制要求日志文件必须已存在：有些 bat 会在稍后创建/覆盖
        ptr.write_text(str(p), encoding="utf-8")
    except Exception:
        logger.debug("写入外部日志指针失败", exc_info=True)


def _maybe_record_external_log_path(*, task_name: str, log_root: str, text: str) -> None:
    """从输出中提取 .log 路径并写入指针文件，供 UI 直接打开外部日志。"""
    try:
        if not text:
            return
        # Windows 路径 + .log，尽量宽松匹配（允许空格前后用引号包裹）
        candidates = re.findall(r"([A-Za-z]:\\\\[^\\r\\n\"']+?\\.log)", text)
        if not candidates:
            return
        # 取最后一个更可能是最终日志文件
        for raw in reversed(candidates):
            p = Path(raw)
            if p.is_file():
                ptr = _external_log_pointer_path(task_name, log_root)
                ptr.write_text(str(p.resolve()), encoding="utf-8")
                return
    except Exception:
        logger.debug("记录外部日志路径失败", exc_info=True)


def _maybe_record_external_log_path_from_fs(
    *,
    task_name: str,
    log_root: str,
    working_directory: str | None,
    command_path: str,
    started_at: float,
    finished_at: float,
) -> None:
    """
    从文件系统兜底探测“任务生成的日志文件”并写入指针。

    触发条件:
    - stdout/stderr 未解析出日志路径
    - 任务确实在运行期间生成/更新了某个 .log 文件

    策略:
    - 搜索 working_directory（优先）或命令所在目录下的 *.log
    - 选取在 [started_at, finished_at + 2s] 时间窗口内修改过的最新文件
    """
    try:
        ptr = _external_log_pointer_path(task_name, log_root)
        if ptr.is_file():
            # 已有指针（通常来自 stdout/stderr），不覆盖
            return

        base_dir = Path(working_directory) if working_directory else Path(command_path).resolve().parent
        if not base_dir.exists():
            return

        # 给文件系统时间戳/缓冲一点余量
        window_end = finished_at + 2.0
        newest: tuple[float, Path] | None = None
        for p in base_dir.glob("*.log"):
            try:
                st = p.stat()
            except OSError:
                continue
            mtime = float(st.st_mtime)
            if started_at <= mtime <= window_end:
                if newest is None or mtime > newest[0]:
                    newest = (mtime, p)

        if newest is None:
            return
        target = newest[1]
        if target.is_file():
            ptr.write_text(str(target.resolve()), encoding="utf-8")
    except Exception:
        logger.debug("文件系统探测外部日志失败", exc_info=True)


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
