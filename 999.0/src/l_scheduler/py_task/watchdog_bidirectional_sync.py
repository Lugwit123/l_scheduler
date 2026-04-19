#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
watchdog 双向同步任务（示例）。

用途：
- 支持在 l_scheduler 的 task_files.json 中把 path 配置为 .py 文件。
- 本脚本用 watchdog 监听两侧目录变更，并同步到对侧。

注意：
- 需先安装 watchdog：pip install watchdog
- 该脚本是“持续运行型”任务，建议在 task_files.json 里只启动一个实例。
"""

from __future__ import annotations

import argparse
import shutil
import threading
import time
from pathlib import Path
from typing import Iterable

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
except ImportError:
    print("ERROR: 未安装 watchdog，请先执行: pip install watchdog")
    raise SystemExit(2)

DEFAULT_LEFT_DIR = (
    "D:/TD_Depot/Wuzu_dev/anim_upload_muse_tool/src/anim_upload_muse_tool/samba_to_muse"
)
DEFAULT_RIGHT_DIR = "L:/temp/j_muse_backup/samba_to_muse"


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


def _iter_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file()]


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

    def on_created(self, event):  # type: ignore[override]
        if event.is_directory:
            try:
                self._to_dst(event.src_path).mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                print(f"[{self.name}] create-dir error: {exc}")
            return
        if self._is_ignored(event.src_path):
            return
        src = Path(event.src_path)
        src_key = str(src.resolve())
        if self._is_suppressed(src_key):
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
            print(f"[{self.name}] created error: {exc}")

    def on_modified(self, event):  # type: ignore[override]
        if event.is_directory:
            return
        if self._is_ignored(event.src_path):
            return
        src = Path(event.src_path)
        src_key = str(src.resolve())
        if self._is_suppressed(src_key):
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
            print(f"[{self.name}] modified error: {exc}")

    def on_moved(self, event):  # type: ignore[override]
        if self._is_ignored(event.src_path) or self._is_ignored(event.dest_path):
            return
        src_key = str(Path(event.src_path).resolve())
        if self._is_suppressed(src_key):
            return
        try:
            old_dst = self._to_dst(event.src_path)
            new_dst = self._to_dst(event.dest_path)
            # 安全保护：永不删除同步根目录；默认不传播“删除语义”。
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
            print(f"[{self.name}] moved error: {exc}")

    def _confirm_deleted_then_handle(self, src_path: str) -> None:
        time.sleep(self.delete_confirm_delay_sec)
        src = Path(src_path)
        if src.exists():
            # 短暂断连或 watcher 抖动，不当作真实删除。
            print(f"[{self.name}] deleted ignored (path recovered): {src_path}")
            return
        src_key = str(src.resolve())
        if self._is_suppressed(src_key):
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
            print(f"[{self.name}] deleted error: {exc}")

    def on_deleted(self, event):  # type: ignore[override]
        if self._is_ignored(event.src_path):
            return
        worker = threading.Thread(
            target=self._confirm_deleted_then_handle,
            args=(event.src_path,),
            daemon=True,
        )
        worker.start()


def _wait_path_ready(path: Path, retries: int, interval_sec: float) -> bool:
    """等待目录可访问（存在且可枚举）。"""
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


def main() -> int:
    parser = argparse.ArgumentParser(description="watchdog 双向目录同步")
    parser.add_argument(
        "--left",
        default=DEFAULT_LEFT_DIR,
        help=f"左侧目录（默认: {DEFAULT_LEFT_DIR}）",
    )
    parser.add_argument(
        "--right",
        default=DEFAULT_RIGHT_DIR,
        help=f"右侧目录（默认: {DEFAULT_RIGHT_DIR}）",
    )
    parser.add_argument(
        "--no-initial-sync",
        action="store_true",
        help="关闭启动前的一次双向初始化同步（默认会执行）",
    )
    parser.add_argument(
        "--suppress-ttl-seconds",
        type=float,
        default=1.5,
        help="写回事件抑制窗口（秒），默认 1.5",
    )
    parser.add_argument(
        "--ignore-suffixes",
        type=str,
        default=".tmp,.swp,.swx,.log,.cache,.bak",
        help="忽略后缀列表，逗号分隔",
    )
    parser.add_argument(
        "--propagate-delete",
        action="store_true",
        help="启用删除同步（默认关闭，避免网络波动导致误删）",
    )
    parser.add_argument(
        "--delete-confirm-delay-seconds",
        type=float,
        default=3.0,
        help="删除事件确认延迟（秒），默认 3.0",
    )
    parser.add_argument(
        "--ready-check-retries",
        type=int,
        default=5,
        help="启动前目录可访问性检查重试次数，默认 5",
    )
    parser.add_argument(
        "--ready-check-interval-seconds",
        type=float,
        default=1.0,
        help="启动前目录可访问性检查重试间隔（秒），默认 1.0",
    )
    args = parser.parse_args()

    left = Path(args.left).resolve()
    right = Path(args.right).resolve()
    if not _wait_path_ready(left, args.ready_check_retries, args.ready_check_interval_seconds):
        print(f"ERROR: left path not ready: {left}")
        return 3
    if not _wait_path_ready(right, args.ready_check_retries, args.ready_check_interval_seconds):
        print(f"ERROR: right path not ready: {right}")
        return 4

    if not args.no_initial_sync:
        print(f"[init] bidirectional sync: {left} <-> {right}")
        _initial_bidirectional_sync(left, right)

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
        )
    left_to_right.propagate_delete = args.propagate_delete
    observer.schedule(
        left_to_right,
        str(left),
        recursive=True,
    )
    right_to_left = MirrorHandler(
            src_root=right,
            dst_root=left,
            name="R->L",
            suppress_map=suppress_map,
            suppress_ttl_sec=args.suppress_ttl_seconds,
            ignore_suffixes=ignore_suffixes,
        delete_confirm_delay_sec=args.delete_confirm_delay_seconds,
        )
    right_to_left.propagate_delete = args.propagate_delete
    observer.schedule(
        right_to_left,
        str(right),
        recursive=True,
    )
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

