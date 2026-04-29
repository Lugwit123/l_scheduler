#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
watchdog 双向同步任务。

配置优先级（高 → 低）：
  1. CLI 参数（显式传入时覆盖 yaml）
  2. setting.yaml（同目录）
  3. 内置默认值

报错通知：
  运行期间如果发生无法忽略的错误，会按 setting.yaml 中 error_notify 的配置发出通知，
  支持桌面气泡（plyer）、邮件（smtplib）、HTTP Webhook 三种方式。
"""

from __future__ import annotations

import argparse
import atexit
import copy
import os
import shutil
import smtplib
import sys
import threading
import time
import urllib.request
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Iterable

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
except ImportError:
    print("ERROR: 未安装 watchdog，请先执行: pip install watchdog")
    raise SystemExit(2)

_SETTING_FILE = Path(__file__).resolve().parent / "setting.yaml"

# ── 默认值（与 setting.yaml 结构对应） ────────────────────────────────────
_DEFAULTS: dict[str, Any] = {
    "sync": {"left": "", "right": ""},
    "behavior": {
        "initial_sync": True,
        "propagate_delete": False,
        "suppress_ttl_seconds": 1.5,
        "delete_confirm_delay_seconds": 3.0,
        "ignore_suffixes": ".tmp,.swp,.swx,.log,.cache,.bak,.pyc,.pyo,.pyd",
        "ignore_dirs": ".git,.svn,.hg,.idea,__pycache__,.mypy_cache,build,dist,.tox,.venv,venv,node_modules,py_312,packages",
    },
    "ready_check": {"retries": 5, "interval_seconds": 1.0},
    "error_notify": {
        "enabled": False,
        "desktop": {"enabled": True},
        "email": {
            "enabled": False,
            "smtp_host": "",
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


def _deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _load_yaml_cfg() -> dict:
    """从 setting.yaml 加载配置，缺失键用默认值补全。"""
    if not _SETTING_FILE.exists():
        return copy.deepcopy(_DEFAULTS)
    try:
        import yaml
        raw = yaml.safe_load(_SETTING_FILE.read_text(encoding="utf-8")) or {}
        if isinstance(raw, dict):
            return _deep_merge(_DEFAULTS, raw)
    except Exception as exc:
        print(f"[config] WARNING: 读取 setting.yaml 失败，使用默认值: {exc}")
    return copy.deepcopy(_DEFAULTS)


# ── 报错通知 ───────────────────────────────────────────────────────────────

def _notify_error(message: str, notify_cfg: dict) -> None:
    """按配置分发报错通知，在独立线程中静默执行（不阻塞主流程）。"""
    if not notify_cfg.get("enabled", False):
        return

    def _send() -> None:
        # 1. 桌面气泡
        if notify_cfg.get("desktop", {}).get("enabled", False):
            try:
                from plyer import notification  # type: ignore[import]
                notification.notify(
                    title="watchdog 同步错误",
                    message=message[:200],
                    timeout=8,
                )
            except Exception as exc:
                print(f"[notify] desktop failed: {exc}")

        # 2. 邮件
        em = notify_cfg.get("email", {})
        if em.get("enabled", False):
            try:
                msg = MIMEText(message, "plain", "utf-8")
                msg["Subject"] = "watchdog 同步错误"
                msg["From"] = em.get("from_addr", "")
                to_addrs = [a.strip() for a in str(em.get("to_addrs", "")).split(",") if a.strip()]
                msg["To"] = ", ".join(to_addrs)
                port = int(em.get("smtp_port", 465))
                host = em.get("smtp_host", "")
                if em.get("smtp_ssl", True):
                    with smtplib.SMTP_SSL(host, port) as s:
                        s.login(em.get("username", ""), em.get("password", ""))
                        s.sendmail(msg["From"], to_addrs, msg.as_string())
                else:
                    with smtplib.SMTP(host, port) as s:
                        s.starttls()
                        s.login(em.get("username", ""), em.get("password", ""))
                        s.sendmail(msg["From"], to_addrs, msg.as_string())
            except Exception as exc:
                print(f"[notify] email failed: {exc}")

        # 3. Webhook
        wh = notify_cfg.get("webhook", {})
        if wh.get("enabled", False) and wh.get("url"):
            try:
                body_tpl = wh.get("body_template", "{message}")
                body = body_tpl.replace("{message}", message.replace('"', '\\"'))
                data = body.encode("utf-8")
                req = urllib.request.Request(wh["url"], data=data, method="POST")
                # 解析 headers（每行 Key: Value）
                for line in str(wh.get("headers", "")).splitlines():
                    if ":" in line:
                        k, _, v = line.partition(":")
                        req.add_header(k.strip(), v.strip())
                with urllib.request.urlopen(req, timeout=10):
                    pass
            except Exception as exc:
                print(f"[notify] webhook failed: {exc}")

    t = threading.Thread(target=_send, daemon=True)
    t.start()


# ── 文件操作工具 ───────────────────────────────────────────────────────────

def _safe_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _safe_remove(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    elif path.exists():
        path.unlink(missing_ok=True)


def _same_file(src: Path, dst: Path) -> bool:
    """粗粒度判断两个文件是否一致，避免无意义回写。"""
    if not src.exists() or not dst.exists():
        return False
    src_stat = src.stat()
    dst_stat = dst.stat()
    return (
        src_stat.st_size == dst_stat.st_size
        and src_stat.st_mtime_ns == dst_stat.st_mtime_ns
    )


_DEFAULT_IGNORE_DIRS: frozenset[str] = frozenset(
    d.strip() for d in _DEFAULTS["behavior"]["ignore_dirs"].split(",") if d.strip()
)
_DEFAULT_IGNORE_SUFFIXES: frozenset[str] = frozenset(
    s.strip().lower() for s in _DEFAULTS["behavior"]["ignore_suffixes"].split(",") if s.strip()
)

_LOCK_FILE = Path(__file__).resolve().parent / ".watchdog_sync.pid"


def _acquire_singleton() -> bool:
    """PID 单例锁：已有实例运行则返回 False。"""
    if _LOCK_FILE.exists():
        try:
            pid = int(_LOCK_FILE.read_text(encoding="utf-8").strip())
            if pid != os.getpid():
                alive = False
                try:
                    if sys.platform == "win32":
                        import ctypes
                        h = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
                        if h:
                            ctypes.windll.kernel32.CloseHandle(h)
                            alive = True
                    else:
                        os.kill(pid, 0)
                        alive = True
                except Exception:
                    pass
                if alive:
                    print(f"[singleton] watchdog_bidirectional_sync 已在运行 (PID {pid})，跳过本次调度")
                    return False
        except Exception:
            pass
    _LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")
    atexit.register(lambda: _LOCK_FILE.unlink(missing_ok=True))
    return True


def _iter_files(
    root: Path,
    ignore_dirs: frozenset[str] = _DEFAULT_IGNORE_DIRS,
    ignore_suffixes: frozenset[str] = _DEFAULT_IGNORE_SUFFIXES,
) -> list[Path]:
    result: list[Path] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() in ignore_suffixes or p.name.startswith("~$"):
            continue
        try:
            rel = p.relative_to(root)
            if any(part in ignore_dirs for part in rel.parts):
                continue
        except ValueError:
            continue
        result.append(p)
    return result


def _relative_map(root: Path) -> dict[Path, Path]:
    file_map: dict[Path, Path] = {}
    for file_path in _iter_files(root):
        try:
            rel = file_path.relative_to(root)
        except ValueError:
            continue
        file_map[rel] = file_path
    return file_map


def _initial_bidirectional_sync(left: Path, right: Path) -> None:
    """
    启动前做一次双向收敛：
    - 任一侧独有文件复制到另一侧
    - 两侧同名文件以 mtime_ns 较新者覆盖较旧者
    """
    left_map = _relative_map(left)
    right_map = _relative_map(right)
    all_rel_paths = set(left_map.keys()) | set(right_map.keys())

    synced = 0
    for rel in sorted(all_rel_paths):
        left_file = left_map.get(rel)
        right_file = right_map.get(rel)
        left_dst = left / rel
        right_dst = right / rel

        if left_file is None and right_file is not None:
            _safe_copy(right_file, left_dst)
            synced += 1
            continue
        if right_file is None and left_file is not None:
            _safe_copy(left_file, right_dst)
            synced += 1
            continue
        if left_file is None or right_file is None:
            continue
        if _same_file(left_file, right_file):
            continue

        left_stat = left_file.stat()
        right_stat = right_file.stat()
        if left_stat.st_mtime_ns >= right_stat.st_mtime_ns:
            _safe_copy(left_file, right_dst)
        else:
            _safe_copy(right_file, left_dst)
        synced += 1

    print(f"[init] bidirectional sync done, changed files: {synced}")


# ── MirrorHandler ──────────────────────────────────────────────────────────

class MirrorHandler(FileSystemEventHandler):
    def __init__(
        self,
        src_root: Path,
        dst_root: Path,
        name: str,
        suppress_map: dict[str, float],
        suppress_ttl_sec: float,
        ignore_suffixes: Iterable[str],
        delete_confirm_delay_sec: float,
        notify_cfg: dict,
    ) -> None:
        super().__init__()
        self.src_root = src_root
        self.dst_root = dst_root
        self.name = name
        self.suppress_map = suppress_map
        self.suppress_ttl_sec = suppress_ttl_sec
        self.ignore_suffixes = tuple(ignore_suffixes)
        self.propagate_delete = False
        self.delete_confirm_delay_sec = delete_confirm_delay_sec
        self.notify_cfg = notify_cfg

    def _to_dst(self, src_path: str) -> Path:
        rel = Path(src_path).resolve().relative_to(self.src_root)
        return self.dst_root / rel

    def _is_root_path(self, path: Path, root: Path) -> bool:
        try:
            return path.resolve() == root.resolve()
        except Exception:
            return False

    def _is_ignored(self, path: str) -> bool:
        p = Path(path)
        if p.name.startswith("~$"):
            return True
        try:
            rel = p.resolve().relative_to(self.src_root)
            if any(part in _DEFAULT_IGNORE_DIRS for part in rel.parts):
                return True
        except ValueError:
            pass
        suffix = p.suffix.lower()
        return suffix in self.ignore_suffixes

    def _is_suppressed(self, path: str) -> bool:
        now = time.time()
        exp = self.suppress_map.get(path)
        if exp is None:
            return False
        if exp > now:
            return True
        del self.suppress_map[path]
        return False

    def _mark_suppressed(self, path: Path) -> None:
        self.suppress_map[str(path.resolve())] = time.time() + self.suppress_ttl_sec

    def _report_error(self, context: str, exc: Exception) -> None:
        msg = f"[{self.name}] {context}: {exc}"
        print(msg)
        _notify_error(msg, self.notify_cfg)

    def on_created(self, event):  # type: ignore[override]
        if event.is_directory:
            try:
                self._to_dst(event.src_path).mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                self._report_error("create-dir error", exc)
            return
        if self._is_ignored(event.src_path):
            return
        src = Path(event.src_path)
        if self._is_suppressed(str(src.resolve())):
            return
        try:
            dst = self._to_dst(event.src_path)
            if src.exists():
                if _same_file(src, dst):
                    return
                _safe_copy(src, dst)
                self._mark_suppressed(dst)
                print(f"[{self.name}] created -> {dst}")
        except Exception as exc:
            self._report_error("created error", exc)

    def on_modified(self, event):  # type: ignore[override]
        if event.is_directory:
            return
        if self._is_ignored(event.src_path):
            return
        src = Path(event.src_path)
        if self._is_suppressed(str(src.resolve())):
            return
        try:
            dst = self._to_dst(event.src_path)
            if src.exists():
                if _same_file(src, dst):
                    return
                _safe_copy(src, dst)
                self._mark_suppressed(dst)
                print(f"[{self.name}] modified -> {dst}")
        except Exception as exc:
            self._report_error("modified error", exc)

    def on_moved(self, event):  # type: ignore[override]
        if self._is_ignored(event.src_path) or self._is_ignored(event.dest_path):
            return
        if self._is_suppressed(str(Path(event.src_path).resolve())):
            return
        try:
            old_dst = self._to_dst(event.src_path)
            new_dst = self._to_dst(event.dest_path)
            if self._is_root_path(old_dst, self.dst_root):
                print(f"[{self.name}] skip dangerous root move deletion: {old_dst}")
                return
            if self.propagate_delete:
                _safe_remove(old_dst)
            if not event.is_directory and Path(event.dest_path).exists():
                src = Path(event.dest_path)
                if not _same_file(src, new_dst):
                    _safe_copy(src, new_dst)
                self._mark_suppressed(new_dst)
            else:
                new_dst.mkdir(parents=True, exist_ok=True)
                self._mark_suppressed(new_dst)
            print(f"[{self.name}] moved -> {new_dst}")
        except Exception as exc:
            self._report_error("moved error", exc)

    def _confirm_deleted_then_handle(self, src_path: str) -> None:
        time.sleep(self.delete_confirm_delay_sec)
        src = Path(src_path)
        if src.exists():
            print(f"[{self.name}] deleted ignored (path recovered): {src_path}")
            return
        if self._is_suppressed(str(src.resolve())):
            return
        try:
            if not self.propagate_delete:
                print(f"[{self.name}] deleted (dry-run confirmed) -> {src_path}")
                return
            dst = self._to_dst(src_path)
            if self._is_root_path(dst, self.dst_root):
                print(f"[{self.name}] skip dangerous root delete: {dst}")
                return
            _safe_remove(dst)
            self._mark_suppressed(dst)
            print(f"[{self.name}] deleted -> {dst}")
        except Exception as exc:
            self._report_error("deleted error", exc)

    def on_deleted(self, event):  # type: ignore[override]
        if self._is_ignored(event.src_path):
            return
        worker = threading.Thread(
            target=self._confirm_deleted_then_handle,
            args=(event.src_path,),
            daemon=True,
        )
        worker.start()


# ── 目录可访问性检查 ───────────────────────────────────────────────────────

def _wait_path_ready(path: Path, retries: int, interval_sec: float) -> bool:
    for attempt in range(1, retries + 1):
        try:
            path.mkdir(parents=True, exist_ok=True)
            list(path.iterdir())
            return True
        except Exception as exc:
            print(f"[ready] attempt {attempt}/{retries} failed for {path}: {exc}")
            if attempt < retries:
                time.sleep(interval_sec)
    return False


# ── main ───────────────────────────────────────────────────────────────────

def main() -> int:
    # 单例检查：已有实例运行则直接退出（避免定时调度重复启动）
    if not _acquire_singleton():
        return 0

    # 先从 yaml 读取基础配置
    yaml_cfg = _load_yaml_cfg()
    y_sync = yaml_cfg.get("sync", {})
    y_beh = yaml_cfg.get("behavior", {})
    y_rc = yaml_cfg.get("ready_check", {})
    notify_cfg = yaml_cfg.get("error_notify", {})

    parser = argparse.ArgumentParser(
        description="watchdog 双向目录同步（参数可覆盖 setting.yaml）"
    )
    parser.add_argument("--left", default=y_sync.get("left", ""),
                        help="左侧目录（覆盖 yaml）")
    parser.add_argument("--right", default=y_sync.get("right", ""),
                        help="右侧目录（覆盖 yaml）")
    parser.add_argument("--no-initial-sync", action="store_true",
                        default=not bool(y_beh.get("initial_sync", True)),
                        help="关闭启动前双向初始化同步")
    parser.add_argument("--suppress-ttl-seconds", type=float,
                        default=float(y_beh.get("suppress_ttl_seconds", 1.5)))
    parser.add_argument("--ignore-suffixes", type=str,
                        default=str(y_beh.get("ignore_suffixes", ".tmp,.swp,.swx,.log,.cache,.bak")))
    parser.add_argument("--propagate-delete", action="store_true",
                        default=bool(y_beh.get("propagate_delete", False)))
    parser.add_argument("--delete-confirm-delay-seconds", type=float,
                        default=float(y_beh.get("delete_confirm_delay_seconds", 3.0)))
    parser.add_argument("--ready-check-retries", type=int,
                        default=int(y_rc.get("retries", 5)))
    parser.add_argument("--ready-check-interval-seconds", type=float,
                        default=float(y_rc.get("interval_seconds", 1.0)))
    args = parser.parse_args()

    if not args.left or not args.right:
        msg = "ERROR: 未配置同步目录，请在 setting.yaml 中设置 sync.left 和 sync.right，或通过 --left/--right 参数传入。"
        print(msg)
        _notify_error(msg, notify_cfg)
        return 1

    left = Path(args.left).resolve()
    right = Path(args.right).resolve()

    if not _wait_path_ready(left, args.ready_check_retries, args.ready_check_interval_seconds):
        msg = f"ERROR: left path not ready: {left}"
        print(msg)
        _notify_error(msg, notify_cfg)
        return 3
    if not _wait_path_ready(right, args.ready_check_retries, args.ready_check_interval_seconds):
        msg = f"ERROR: right path not ready: {right}"
        print(msg)
        _notify_error(msg, notify_cfg)
        return 4

    if not args.no_initial_sync:
        print(f"[init] bidirectional sync: {left} <-> {right}")
        try:
            _initial_bidirectional_sync(left, right)
        except Exception as exc:
            msg = f"ERROR: initial sync failed: {exc}"
            print(msg)
            _notify_error(msg, notify_cfg)
            return 5

    suppress_map: dict[str, float] = {}
    ignore_suffixes = [x.strip().lower() for x in args.ignore_suffixes.split(",") if x.strip()]

    observer = Observer()
    left_to_right = MirrorHandler(
        src_root=left,
        dst_root=right,
        name="L->R",
        suppress_map=suppress_map,
        suppress_ttl_sec=args.suppress_ttl_seconds,
        ignore_suffixes=ignore_suffixes,
        delete_confirm_delay_sec=args.delete_confirm_delay_seconds,
        notify_cfg=notify_cfg,
    )
    left_to_right.propagate_delete = args.propagate_delete
    observer.schedule(left_to_right, str(left), recursive=True)

    right_to_left = MirrorHandler(
        src_root=right,
        dst_root=left,
        name="R->L",
        suppress_map=suppress_map,
        suppress_ttl_sec=args.suppress_ttl_seconds,
        ignore_suffixes=ignore_suffixes,
        delete_confirm_delay_sec=args.delete_confirm_delay_seconds,
        notify_cfg=notify_cfg,
    )
    right_to_left.propagate_delete = args.propagate_delete
    observer.schedule(right_to_left, str(right), recursive=True)

    observer.start()
    print(f"[watch] started: {left} <-> {right}")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
