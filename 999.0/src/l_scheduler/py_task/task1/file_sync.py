# -*- coding: utf-8 -*-
"""
单文件同步任务（file_sync）。

功能：
  - 从 setting.yaml 读取 file_sync.pairs（源文件 → 目标文件 的对应关系）
  - 使用 watchdog 监听所有源文件所在目录
  - 任何源文件发生写入/创建/移动变更时，立即将其复制到对应目标路径
  - 目标文件的父目录不存在时自动创建
  - 支持启动时全量复制（copy_on_start）
  - 发生错误时通过 setting.yaml error_notify 配置的通道发出通知

用法：
  python file_sync.py [--config /path/to/setting.yaml]
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── yaml（可选，优雅降级）──────────────────────────────────────────────────

try:
    import yaml as _yaml
except ImportError:
    _yaml = None  # type: ignore[assignment]

# ── watchdog（必须）───────────────────────────────────────────────────────

try:
    from watchdog.events import (
        FileCreatedEvent,
        FileModifiedEvent,
        FileMovedEvent,
        FileSystemEventHandler,
    )
    from watchdog.observers import Observer
except ImportError as _e:
    sys.exit(f"[file_sync] 缺少依赖 watchdog：{_e}\n请执行：pip install watchdog")

# ─────────────────────────────────────────────────────────────────────────────
# 配置加载
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_CONFIG: dict[str, Any] = {
    "file_sync": {
        "pairs": [],
        "copy_on_start": True,
    },
    "error_notify": {
        "enabled": False,
        "desktop": {"enabled": True},
        "email": {
            "enabled": False,
            "smtp_host": "smtp.example.com",
            "smtp_port": 465,
            "smtp_ssl": True,
            "username": "",
            "password": "",
            "from_addr": "",
            "to_addrs": "",
        },
        "webhook": {
            "enabled": False,
            "url": "",
            "body_template": '{"msgtype":"text","text":{"content":"{message}"}}',
            "headers": "Content-Type: application/json",
        },
    },
}

_DEFAULT_YAML_PATH = Path(__file__).resolve().parent / "setting.yaml"


def _deep_merge(base: dict, override: dict) -> dict:
    import copy
    result = copy.deepcopy(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _load_config(yaml_path: Path) -> dict[str, Any]:
    if _yaml is None:
        logger.warning("[file_sync] PyYAML 未安装，使用内置默认配置。")
        import copy
        return copy.deepcopy(_DEFAULT_CONFIG)
    if yaml_path.exists():
        try:
            raw = _yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            if isinstance(raw, dict):
                return _deep_merge(_DEFAULT_CONFIG, raw)
        except Exception as exc:
            logger.warning("[file_sync] 读取 setting.yaml 失败：%s", exc)
    import copy
    return copy.deepcopy(_DEFAULT_CONFIG)


# ─────────────────────────────────────────────────────────────────────────────
# 报错通知
# ─────────────────────────────────────────────────────────────────────────────

def _notify_error(message: str, notify_cfg: dict) -> None:
    """在 daemon 线程中异步分发错误通知，不阻塞主流程。"""
    if not notify_cfg.get("enabled", False):
        return

    def _run() -> None:
        # ── 桌面气泡 ──
        desktop_cfg = notify_cfg.get("desktop", {})
        if desktop_cfg.get("enabled", True):
            try:
                from plyer import notification  # type: ignore
                notification.notify(
                    title="[file_sync] 同步错误",
                    message=message[:256],
                    timeout=8,
                )
            except Exception:
                try:
                    from win10toast import ToastNotifier  # type: ignore
                    ToastNotifier().show_toast(
                        "[file_sync] 同步错误",
                        message[:256],
                        duration=8,
                        threaded=True,
                    )
                except Exception:
                    pass  # 桌面通知失败不影响其他通道

        # ── 邮件 ──
        em = notify_cfg.get("email", {})
        if em.get("enabled", False):
            try:
                import smtplib
                from email.mime.text import MIMEText
                msg = MIMEText(message, "plain", "utf-8")
                msg["Subject"] = "[file_sync] 同步错误"
                msg["From"] = em.get("from_addr", "")
                msg["To"] = em.get("to_addrs", "")
                host = em.get("smtp_host", "")
                port = int(em.get("smtp_port", 465))
                use_ssl = em.get("smtp_ssl", True)
                if use_ssl:
                    srv = smtplib.SMTP_SSL(host, port, timeout=15)
                else:
                    srv = smtplib.SMTP(host, port, timeout=15)
                srv.login(em.get("username", ""), em.get("password", ""))
                srv.sendmail(
                    em.get("from_addr", ""),
                    [a.strip() for a in em.get("to_addrs", "").split(",") if a.strip()],
                    msg.as_string(),
                )
                srv.quit()
            except Exception as exc:
                logger.error("[file_sync] 邮件通知失败：%s", exc)

        # ── Webhook ──
        wh = notify_cfg.get("webhook", {})
        if wh.get("enabled", False):
            try:
                import urllib.request
                import json as _json
                url = wh.get("url", "")
                body = wh.get("body_template", "{message}").replace("{message}", message)
                headers_raw = wh.get("headers", "")
                req = urllib.request.Request(url, data=body.encode("utf-8"), method="POST")
                for line in headers_raw.splitlines():
                    line = line.strip()
                    if ":" in line:
                        k, v = line.split(":", 1)
                        req.add_header(k.strip(), v.strip())
                with urllib.request.urlopen(req, timeout=15):
                    pass
            except Exception as exc:
                logger.error("[file_sync] Webhook 通知失败：%s", exc)

    t = threading.Thread(target=_run, daemon=True)
    t.start()


# ─────────────────────────────────────────────────────────────────────────────
# 文件复制
# ─────────────────────────────────────────────────────────────────────────────

def _copy_file(src: Path, dst: Path, notify_cfg: dict) -> None:
    """将 src 复制到 dst，目标父目录不存在时自动创建。"""
    try:
        if not src.is_file():
            logger.warning("[file_sync] 源文件不存在，跳过：%s", src)
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        logger.info("[file_sync] 已同步：%s  →  %s", src, dst)
    except Exception as exc:
        msg = f"复制失败 {src} → {dst}：{exc}"
        logger.error("[file_sync] %s", msg)
        _notify_error(msg, notify_cfg)


# ─────────────────────────────────────────────────────────────────────────────
# watchdog 事件处理
# ─────────────────────────────────────────────────────────────────────────────

class FileSyncHandler(FileSystemEventHandler):
    """
    监听若干源文件；任一源文件发生写入/创建/移动时，
    将其复制到对应目标路径。
    """

    def __init__(
        self,
        src_to_dst: dict[str, str],  # src 绝对路径字符串 → dst 绝对路径字符串
        notify_cfg: dict,
    ) -> None:
        super().__init__()
        # 统一转为小写路径键（Windows 大小写不敏感）
        self._src_to_dst: dict[str, str] = {
            str(Path(k).resolve()).lower(): v for k, v in src_to_dst.items()
        }
        self._notify_cfg = notify_cfg

    def _handle(self, src_path: str) -> None:
        key = src_path.lower()
        dst_str = self._src_to_dst.get(key)
        if dst_str is None:
            return
        _copy_file(Path(src_path), Path(dst_str), self._notify_cfg)

    def on_modified(self, event) -> None:  # type: ignore[override]
        if not event.is_directory:
            self._handle(event.src_path)

    def on_created(self, event) -> None:  # type: ignore[override]
        if not event.is_directory:
            self._handle(event.src_path)

    def on_moved(self, event) -> None:  # type: ignore[override]
        if not event.is_directory:
            # 目标路径变成了新文件，也检查一下
            self._handle(event.dest_path)


# ─────────────────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="单文件同步：监听源文件变化，自动复制到目标路径。"
    )
    parser.add_argument(
        "--config",
        default=str(_DEFAULT_YAML_PATH),
        help=f"setting.yaml 路径（默认：{_DEFAULT_YAML_PATH}）",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    cfg = _load_config(Path(args.config))
    fs_cfg = cfg.get("file_sync", {})
    notify_cfg = cfg.get("error_notify", {})

    pairs_raw = fs_cfg.get("pairs") or []
    # 过滤有效对
    pairs: list[tuple[Path, Path]] = []
    for item in pairs_raw:
        if not isinstance(item, dict):
            continue
        src_str = str(item.get("src", "")).strip()
        dst_str = str(item.get("dst", "")).strip()
        if src_str and dst_str:
            pairs.append((Path(src_str).resolve(), Path(dst_str).resolve()))

    if not pairs:
        logger.warning("[file_sync] setting.yaml 中 file_sync.pairs 为空，无文件需要同步，退出。")
        _notify_error(
            "file_sync 未配置任何文件对（file_sync.pairs 为空），任务退出。",
            notify_cfg,
        )
        return

    # 检查源文件是否可访问
    missing = [str(src) for src, _ in pairs if not src.exists()]
    if missing:
        msg = "以下源文件不存在或不可访问：\n" + "\n".join(f"  • {p}" for p in missing)
        logger.error("[file_sync] %s", msg)
        _notify_error(msg, notify_cfg)
        # 不退出，仍继续监听（文件可能稍后出现）

    # 启动时全量复制
    if fs_cfg.get("copy_on_start", True):
        logger.info("[file_sync] 启动时全量复制...")
        for src, dst in pairs:
            _copy_file(src, dst, notify_cfg)

    # 构建 src→dst 映射（小写 key）及监听目录集合
    src_to_dst: dict[str, str] = {
        str(src): str(dst) for src, dst in pairs
    }
    watch_dirs: set[str] = {str(src.parent) for src, _ in pairs}

    handler = FileSyncHandler(src_to_dst=src_to_dst, notify_cfg=notify_cfg)
    observer = Observer()
    for watch_dir in watch_dirs:
        observer.schedule(handler, watch_dir, recursive=False)
        logger.info("[file_sync] 正在监听目录：%s", watch_dir)

    observer.start()
    logger.info("[file_sync] 监听已启动，共 %d 条文件同步规则。按 Ctrl+C 停止。", len(pairs))

    try:
        while True:
            time.sleep(1)
            if not observer.is_alive():
                raise RuntimeError("Observer 意外退出")
    except (KeyboardInterrupt, SystemExit):
        logger.info("[file_sync] 收到停止信号，正在退出...")
    except Exception as exc:
        msg = f"file_sync 运行时异常：{exc}"
        logger.critical("[file_sync] %s", msg)
        _notify_error(msg, notify_cfg)
    finally:
        observer.stop()
        observer.join()
        logger.info("[file_sync] 已退出。")


if __name__ == "__main__":
    main()
