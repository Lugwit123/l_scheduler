# -*- coding: utf-8 -*-
"""
l_scheduler 入口
用法:
    python main.py                                      # 前台阻塞运行（适合直接启动）
    python main.py --daemon                             # 后台线程运行后进入交互循环
    python main.py --task-files-config <path>           # 仅运行任务文件里的任务
"""
import logging
import argparse
import time
import os
from pathlib import Path

from l_scheduler.scheduler import Scheduler
from l_scheduler.tasks import TaskConfigError, load_task_file_specs, register_file_tasks
from l_scheduler.auth_client import login_password


def setup_logging(log_file: str) -> None:
    """同时输出日志到控制台与文件。"""
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)


def main():
    default_task_config = Path(__file__).resolve().parent / "config" / "task_files.json"

    parser = argparse.ArgumentParser(description="l_scheduler - Lugwit 定时任务调度器")
    parser.add_argument("--daemon", action="store_true", help="后台线程运行，不阻塞")
    parser.add_argument("--ui", action="store_true", help="启动 PySide6 管理界面")
    parser.add_argument("--status", action="store_true", help="打印任务状态后退出")
    parser.add_argument(
        "--instance-tag",
        type=str,
        default="",
        help="实例标记（用于进程识别和窗口标题）",
    )
    parser.add_argument(
        "--task-files-config",
        type=str,
        default=str(default_task_config),
        help="任务文件清单 JSON 路径（支持 .bat/.exe）",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default="logs/l_scheduler.log",
        help="日志输出文件路径，默认 logs/l_scheduler.log",
    )
    parser.add_argument(
        "--auth-url",
        type=str,
        default=os.environ.get("LUGWIT_AUTH_URL", "http://127.0.0.1:1026/api/auth/login"),
        help="统一登录入口（默认指向 ChatRoom /api/auth/login）",
    )
    parser.add_argument(
        "--auth-username",
        type=str,
        default=os.environ.get("LUGWIT_AUTH_USERNAME", ""),
        help="登录用户名（可用环境变量 LUGWIT_AUTH_USERNAME）",
    )
    parser.add_argument(
        "--auth-password",
        type=str,
        default=os.environ.get("LUGWIT_AUTH_PASSWORD", ""),
        help="登录密码（可用环境变量 LUGWIT_AUTH_PASSWORD）",
    )
    parser.add_argument(
        "--auth-nickname",
        type=str,
        default=os.environ.get("LUGWIT_AUTH_NICKNAME", ""),
        help="可选昵称（兼容 ChatRoom 登录表单）",
    )
    args = parser.parse_args()
    effective_log_file = os.environ.get("L_SCHEDULER_LOG_FILE", args.log_file)
    setup_logging(effective_log_file)

    scheduler = Scheduler(tick=1.0)
    effective_task_files_config = os.environ.get(
        "L_SCHEDULER_TASK_FILES_CONFIG",
        args.task_files_config,
    )
    try:
        task_specs = load_task_file_specs(effective_task_files_config)
    except TaskConfigError as exc:
        raise SystemExit(f"任务文件配置错误: {exc}") from exc
    register_file_tasks(scheduler, task_specs)

    # Optional: login and export token to child tasks via env.
    if args.auth_username and args.auth_password:
        try:
            res = login_password(
                auth_url=args.auth_url,
                username=args.auth_username,
                password=args.auth_password,
                nickname=(args.auth_nickname or None),
            )
            os.environ["LUGWIT_ACCESS_TOKEN"] = res.access_token
            os.environ["LUGWIT_TOKEN_TYPE"] = res.token_type
            logging.getLogger("l_scheduler.auth").info("已获取访问令牌，将注入到子任务环境变量 LUGWIT_ACCESS_TOKEN")
        except Exception as exc:
            raise SystemExit(f"认证登录失败: {exc}") from exc

    if args.status:
        for row in scheduler.status():
            print(
                f"  {row['name']:20s}  enabled={row['enabled']}  "
                f"schedule={row['schedule']}  runs={row['run_count']}  "
                f"errors={row['error_count']}  next={row['next_run']}"
            )
        return

    if args.ui:
        scheduler.start(block=False)
        try:
            from l_scheduler.scheduler_ui import run_scheduler_ui
        except ImportError as exc:
            raise SystemExit(
                "启动 UI 失败：缺少 PySide6 依赖。\n"
                "请在当前 Python 3.12 运行时安装 PySide6，或使用不带 --ui 的模式运行。\n"
                f"原始错误: {exc}"
            ) from exc
        run_scheduler_ui(
            scheduler=scheduler,
            task_config_path=effective_task_files_config,
            instance_tag=args.instance_tag,
            log_file=effective_log_file,
        )
        return

    if args.daemon:
        scheduler.start(block=False)
        print("调度器已在后台启动，按 Ctrl+C 退出")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            scheduler.stop()
    else:
        scheduler.start(block=True)


if __name__ == "__main__":
    main()
