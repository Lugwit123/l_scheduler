# -*- coding: utf-8 -*-
"""
task1 统一入口。

根据 setting.yaml 的配置决定运行哪种同步模式：
  - sync.left + sync.right 已填写  → 双向目录同步（watchdog_bidirectional_sync.py）
  - file_sync.pairs 非空           → 单文件同步（file_sync.py）
  - 两者均配置                     → 并行运行两者
  - 均未配置                       → 提示用户完成配置后退出（返回码 1）
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

_HERE = Path(__file__).resolve().parent

# ── 读取配置 ──────────────────────────────────────────────────────────────────

try:
    import yaml as _yaml
    _raw = _yaml.safe_load((_HERE / "setting.yaml").read_text(encoding="utf-8")) or {}
except Exception:
    _raw = {}

_fs_cfg = _raw.get("file_sync") or {}
_sync_cfg = _raw.get("sync") or {}

_has_file_sync = bool(_fs_cfg.get("pairs"))
_has_dir_sync = bool(_sync_cfg.get("left")) and bool(_sync_cfg.get("right"))


# ── 入口 ──────────────────────────────────────────────────────────────────────

def main() -> int:
    if not _has_file_sync and not _has_dir_sync:
        print(
            "[task1] setting.yaml 中未配置任何同步任务。\n"
            "  - 目录同步：请填写 sync.left 和 sync.right\n"
            "  - 文件同步：请在 file_sync.pairs 中添加至少一条记录\n"
            '可通过右键菜单"任务设置..."完成配置。'
        )
        return 1

    # 清空命令行参数，让子脚本的 argparse 以 yaml 默认值运行
    sys.argv = [__file__]

    if _has_dir_sync and not _has_file_sync:
        import watchdog_bidirectional_sync
        return watchdog_bidirectional_sync.main()

    if _has_file_sync and not _has_dir_sync:
        import file_sync
        file_sync.main()
        return 0

    # 两者均配置：并行运行
    import watchdog_bidirectional_sync
    import file_sync

    results: dict[str, int] = {}

    def _run_dir_sync() -> None:
        results["dir_sync"] = watchdog_bidirectional_sync.main()

    def _run_file_sync() -> None:
        file_sync.main()
        results["file_sync"] = 0

    t_dir = threading.Thread(target=_run_dir_sync, name="dir_sync", daemon=True)
    t_file = threading.Thread(target=_run_file_sync, name="file_sync", daemon=True)
    t_dir.start()
    t_file.start()
    t_dir.join()
    t_file.join()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
