import json
from pathlib import Path

import pytest


def _import_tasks():
    # 让 tests 在不依赖 rez 的情况下可运行
    pkg_root = Path(__file__).resolve().parents[1]
    src_root = pkg_root / "src"
    import sys

    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))
    from l_scheduler import tasks as tasks_mod

    return tasks_mod


def test_roundtrip_preserves_external_log_fields(tmp_path: Path):
    tasks = _import_tasks()
    cfg = tmp_path / "task_files.json"
    cfg.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "name": "t1",
                        "path": "sample_task.bat",
                        "enabled": True,
                        "success_return_codes": [0],
                        "schedule": {"type": "interval", "seconds": 5},
                        "working_directory": ".",
                        "external_log_file": "logs/t1.log",
                        "external_log_env_var": "LOG_FILE",
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    specs = tasks.load_task_file_specs(str(cfg))
    assert specs[0]["external_log_file"].endswith(str(Path("logs") / "t1.log"))
    assert specs[0]["external_log_env_var"] == "LOG_FILE"

    # 写回再读入，不应丢字段
    tasks.save_task_file_specs(str(cfg), specs)
    specs2 = tasks.load_task_file_specs(str(cfg))
    assert specs2[0]["external_log_env_var"] == "LOG_FILE"
    assert "external_log_file" in specs2[0]


def test_invalid_external_log_types_raise(tmp_path: Path):
    tasks = _import_tasks()
    cfg = tmp_path / "task_files.json"
    cfg.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "name": "t1",
                        "path": "a.bat",
                        "enabled": True,
                        "success_return_codes": [0],
                        "schedule": {"type": "interval", "seconds": 5},
                        "external_log_file": 123,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(tasks.TaskConfigError):
        tasks.load_task_file_specs(str(cfg))

