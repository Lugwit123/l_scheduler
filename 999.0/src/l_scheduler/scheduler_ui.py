# -*- coding: utf-8 -*-
"""l_scheduler 的 PySide6 管理界面。"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
import ctypes
from pathlib import Path
from typing import Optional, TypeVar

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QComboBox,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QMenu,
    QStyle,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from l_scheduler.scheduler_engine import Scheduler
from l_scheduler.tasks import (
    TaskConfigError,
    TaskFileSpec,
    load_task_file_specs,
    register_file_tasks,
    save_task_file_specs,
    scan_py_task_dir,
)


def _resolve_scheduler_icon(app: QApplication) -> QIcon:
    """Resolve a stable icon for app/window/tray on Windows."""
    candidates = [
        Path(__file__).resolve().parent / "icons" / "l_scheduler.svg",
        Path(__file__).resolve().parent / "icons" / "l_scheduler.ico",
        Path(__file__).resolve().parent / "icons" / "l_scheduler.png",
        Path(__file__).resolve().parent / "l_scheduler.ico",
        Path(__file__).resolve().parent / "l_scheduler.png",
    ]
    for candidate in candidates:
        if candidate.exists():
            icon = QIcon(str(candidate))
            if not icon.isNull():
                return icon
    style = app.style()
    icon = style.standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
    if icon.isNull():
        icon = style.standardIcon(QStyle.StandardPixmap.SP_DesktopIcon)
    if icon.isNull():
        icon = style.standardIcon(QStyle.StandardPixmap.SP_FileIcon)
    return icon


class TaskConfigDialog(QDialog):
    """任务配置编辑对话框。"""

    def __init__(self, config_path: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._config_path = config_path
        self.setWindowTitle("任务设置")
        self.resize(1100, 520)
        self._build_ui()
        self._load_config()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        info = QLabel(f"配置文件: {self._config_path}")
        layout.addWidget(info)

        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels(
            [
                "任务名",
                "路径(.bat/.exe/.py)",
                "定时类型",
                "定时值",
                "启用",
                "参数(空格分隔)",
                "成功返回码(逗号分隔)",
                "工作目录",
                "外部日志文件(可选)",
                "外部日志环境变量(可选)",
            ]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(1, 260)
        self.table.setColumnWidth(7, 220)
        self.table.setColumnWidth(8, 260)
        self.table.setColumnWidth(9, 170)
        layout.addWidget(self.table)

        action_layout = QHBoxLayout()
        self.btn_add = QPushButton("新增任务")
        self.btn_remove = QPushButton("删除任务")
        action_layout.addWidget(self.btn_add)
        action_layout.addWidget(self.btn_remove)
        action_layout.addStretch()
        layout.addLayout(action_layout)

        footer_layout = QHBoxLayout()
        self.btn_save = QPushButton("保存")
        self.btn_cancel = QPushButton("取消")
        footer_layout.addStretch()
        footer_layout.addWidget(self.btn_save)
        footer_layout.addWidget(self.btn_cancel)
        layout.addLayout(footer_layout)

        self.btn_add.clicked.connect(self._add_row)
        self.btn_remove.clicked.connect(self._remove_selected_row)
        self.btn_save.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)

    def _load_config(self) -> None:
        specs = load_task_file_specs(self._config_path)
        self.table.setRowCount(0)
        for spec in specs:
            self._add_row(spec)

    def _add_row(self, spec: Optional[TaskFileSpec] = None) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        values = self._spec_to_row_values(spec)
        for col, value in enumerate(values):
            self.table.setItem(row, col, QTableWidgetItem(value))

    def _remove_selected_row(self) -> None:
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)

    def _spec_to_row_values(self, spec: Optional[TaskFileSpec]) -> list[str]:
        if spec is None:
            return ["", "", "interval", "60", "true", "", "0", "", "", ""]
        schedule_type = spec["schedule_type"]
        if schedule_type == "interval":
            schedule_value = str(spec.get("interval_seconds", 60))
        else:
            schedule_value = str(spec.get("daily_at", "09:00"))
        return [
            spec["name"],
            spec["path"],
            schedule_type,
            schedule_value,
            "true" if spec["enabled"] else "false",
            " ".join(spec.get("arguments", [])),
            ",".join(str(x) for x in spec.get("success_return_codes", [0])),
            spec.get("working_directory", ""),
            spec.get("external_log_file", ""),
            spec.get("external_log_env_var", ""),
        ]

    def get_specs(self) -> list[TaskFileSpec]:
        specs: list[TaskFileSpec] = []
        for row in range(self.table.rowCount()):
            name_item = self.table.item(row, 0)
            path_item = self.table.item(row, 1)
            schedule_type_item = self.table.item(row, 2)
            schedule_value_item = self.table.item(row, 3)
            enabled_item = self.table.item(row, 4)
            args_item = self.table.item(row, 5)
            success_codes_item = self.table.item(row, 6)
            working_directory_item = self.table.item(row, 7)
            external_log_file_item = self.table.item(row, 8)
            external_log_env_var_item = self.table.item(row, 9)

            name = (name_item.text() if name_item is not None else "").strip()
            path = (path_item.text() if path_item is not None else "").strip()
            schedule_type = (schedule_type_item.text() if schedule_type_item is not None else "interval").strip().lower()
            schedule_value = (schedule_value_item.text() if schedule_value_item is not None else "").strip()
            enabled_raw = (enabled_item.text() if enabled_item is not None else "true").strip().lower()
            args_text = (args_item.text() if args_item is not None else "").strip()
            success_codes_text = (success_codes_item.text() if success_codes_item is not None else "0").strip()
            working_directory = (working_directory_item.text() if working_directory_item is not None else "").strip()
            external_log_file = (external_log_file_item.text() if external_log_file_item is not None else "").strip()
            external_log_env_var = (external_log_env_var_item.text() if external_log_env_var_item is not None else "").strip()

            if not name or not path:
                raise TaskConfigError(f"第 {row + 1} 行: 任务名和路径不能为空")
            if schedule_type not in {"interval", "daily"}:
                raise TaskConfigError(f"第 {row + 1} 行: 定时类型仅支持 interval/daily")
            if enabled_raw not in {"true", "false", "1", "0", "yes", "no"}:
                raise TaskConfigError(f"第 {row + 1} 行: 启用列仅支持 true/false")
            enabled = enabled_raw in {"true", "1", "yes"}

            arguments = [x for x in args_text.split(" ") if x] if args_text else []
            try:
                success_return_codes = [
                    int(x.strip())
                    for x in success_codes_text.split(",")
                    if x.strip()
                ]
            except ValueError as exc:
                raise TaskConfigError(f"第 {row + 1} 行: 成功返回码必须是整数列表") from exc
            if not success_return_codes:
                raise TaskConfigError(f"第 {row + 1} 行: 成功返回码不能为空")

            spec: TaskFileSpec = {
                "name": name,
                "path": path,
                "schedule_type": "interval",
                "enabled": enabled,
                "arguments": arguments,
                "success_return_codes": success_return_codes,
            }
            if working_directory:
                spec["working_directory"] = working_directory
            if external_log_file:
                spec["external_log_file"] = external_log_file
            if external_log_env_var:
                spec["external_log_env_var"] = external_log_env_var

            if schedule_type == "interval":
                try:
                    seconds = float(schedule_value)
                except ValueError as exc:
                    raise TaskConfigError(f"第 {row + 1} 行: interval 定时值必须是数字") from exc
                if seconds <= 0:
                    raise TaskConfigError(f"第 {row + 1} 行: interval 秒数必须大于 0")
                spec["schedule_type"] = "interval"
                spec["interval_seconds"] = seconds
            else:
                if not re.match(r"^\d{2}:\d{2}$", schedule_value):
                    raise TaskConfigError(f"第 {row + 1} 行: daily 定时值必须是 HH:MM")
                hh, mm = schedule_value.split(":")
                if not (0 <= int(hh) <= 23 and 0 <= int(mm) <= 59):
                    raise TaskConfigError(f"第 {row + 1} 行: daily 时间超出范围")
                spec["schedule_type"] = "daily"
                spec["daily_at"] = schedule_value

            specs.append(spec)
        return specs


_W = TypeVar("_W", bound=QWidget)


def _require_ui_child(parent: QWidget, typ: type[_W], name: str) -> _W:
    w = parent.findChild(typ, name)
    if w is None:
        raise RuntimeError(f"UI 缺少控件 {name!r}")
    return w


class SchedulerWindow(QMainWindow):
    """调度器管理窗口。"""

    def __init__(
        self,
        scheduler: Scheduler,
        task_config_path: str,
        instance_tag: str = "",
        refresh_interval_ms: int = 1000,
        app_icon: Optional[QIcon] = None,
        log_file: str = "logs/l_scheduler.log",
        py_task_dir: str = "",
    ) -> None:
        super().__init__()
        self._scheduler = scheduler
        self._task_config_path = task_config_path
        self._instance_tag = instance_tag
        self._refresh_interval_ms = refresh_interval_ms
        self._log_file = log_file
        self._py_task_dir = py_task_dir
        self._selected_job_name: Optional[str] = None
        self._tray_icon: Optional[QSystemTrayIcon] = None
        self._force_quit: bool = False
        self._last_job_names: list[str] = []
        self._refresh_count: int = 0

        title = "L Scheduler - 任务管理"
        if self._instance_tag:
            title = f"{title} [{self._instance_tag}]"
        self.setWindowTitle(title)
        if app_icon is not None and not app_icon.isNull():
            self.setWindowIcon(app_icon)
        self.resize(980, 520)
        self._load_ui()
        self._setup_tray_icon()
        self._setup_timer()
        self.refresh_table()
        self._reload_log_view()

    def _load_ui(self) -> None:
        ui_path = Path(__file__).resolve().parent / "scheduler_ui.ui"
        if not ui_path.is_file():
            raise RuntimeError(f"UI 文件不存在: {ui_path}")
        raw = QByteArray(ui_path.read_bytes())
        buf = QBuffer()
        buf.setData(raw)
        if not buf.open(QIODevice.OpenModeFlag.ReadOnly):
            raise RuntimeError(f"无法读取 UI 内容: {ui_path}")
        loader = QUiLoader()
        try:
            central = loader.load(buf, None)
        finally:
            buf.close()
        if central is None:
            raise RuntimeError(f"加载 UI 失败: {ui_path} — {loader.errorString()}")
        self.setCentralWidget(central)

        self._tabs = _require_ui_child(central, QWidget, "mainTabs")
        self.table = _require_ui_child(central, QTableWidget, "taskTable")
        self.status_label = _require_ui_child(central, QLabel, "statusLabel")
        self.btn_refresh = _require_ui_child(central, QPushButton, "refreshButton")
        self.btn_run = _require_ui_child(central, QPushButton, "runButton")
        self.btn_toggle = _require_ui_child(central, QPushButton, "toggleButton")
        self.btn_settings = _require_ui_child(central, QPushButton, "settingsButton")
        self.btn_minimize_to_tray = _require_ui_child(
            central, QPushButton, "minimizeToTrayButton"
        )
        self.btn_restart = _require_ui_child(central, QPushButton, "restartButton")

        self._log_path_label = _require_ui_child(central, QLabel, "logPathLabel")
        self._log_text = _require_ui_child(central, QTextEdit, "logText")
        self._log_refresh_btn = _require_ui_child(
            central, QPushButton, "logRefreshButton"
        )
        self._log_task_combo = _require_ui_child(central, QComboBox, "logTaskCombo")

        self.table.setHorizontalHeaderLabels(
            ["任务名", "启用", "定时", "来源", "执行次数", "失败次数", "最近执行", "下次执行", "状态"]
        )
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(3, 200)

        # 表格右键菜单
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_table_context_menu)
        
        self.btn_refresh.clicked.connect(self.refresh_table)
        self.btn_run.clicked.connect(self.run_selected_job)
        self.btn_toggle.clicked.connect(self.toggle_selected_job)
        self.btn_settings.clicked.connect(self.open_settings)
        self.btn_minimize_to_tray.clicked.connect(self.minimize_to_tray)
        self.btn_restart.clicked.connect(self.restart_app)
        self._log_refresh_btn.clicked.connect(self._reload_log_view)
        self._log_task_combo.currentIndexChanged.connect(self._reload_log_view)
        self._rebuild_log_task_combo()
        self._reload_log_view()

    def _rebuild_log_task_combo(self) -> None:
        cur = self._log_task_combo.currentText().strip()
        self._log_task_combo.blockSignals(True)
        self._log_task_combo.clear()
        self._log_task_combo.addItem("主日志")
        for job in self._scheduler.list_jobs():
            self._log_task_combo.addItem(job.name)
        if cur:
            idx = self._log_task_combo.findText(cur, Qt.MatchFlag.MatchExactly)
            if idx >= 0:
                self._log_task_combo.setCurrentIndex(idx)
        self._log_task_combo.blockSignals(False)

    def _reload_log_view(self) -> None:
        max_tail = 512_000
        sel = self._log_task_combo.currentText().strip() if hasattr(self, "_log_task_combo") else "主日志"
        if not sel or sel == "主日志":
            p = Path(self._log_file)
        else:
            root = Path(self._log_file)
            log_dir = (root.parent if root.suffix else root) / "tasks"
            safe = "".join(ch if (ch.isalnum() or ch in ("-", "_", ".")) else "_" for ch in sel)
            # 优先使用任务记录的“外部日志路径指针”（例如 D:\\Temp\\Log\\sync\\xxx.log）
            ptr = log_dir / f"{safe}.external_log_path.txt"
            if ptr.is_file():
                try:
                    target = Path(ptr.read_text(encoding="utf-8").strip())
                    if target.is_file():
                        p = target
                    else:
                        p = log_dir / f"{safe}.log"
                except OSError:
                    p = log_dir / f"{safe}.log"
            else:
                p = log_dir / f"{safe}.log"
        try:
            self._log_path_label.setText(f"日志文件：{p}")
            if not p.exists():
                self._log_text.setPlainText("（尚无日志文件）")
                return
            size = p.stat().st_size
            with p.open("rb") as f:
                if size > max_tail:
                    f.seek(-max_tail, 2)
                    raw = f.read()
                    nl = raw.find(b"\n")
                    raw = raw[nl + 1 :] if nl >= 0 else raw
                    head = f"...（日志较大，仅显示末尾约 {max_tail // 1024} KB）...\n\n"
                else:
                    raw = f.read()
                    head = ""
            self._log_text.setPlainText(head + raw.decode("utf-8", errors="replace"))
        except OSError as e:
            self._log_text.setPlainText(f"读取日志失败: {e}")

    def _setup_tray_icon(self) -> None:
        """初始化通知栏图标与菜单。"""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        tray_icon = QSystemTrayIcon(self)
        icon = self.windowIcon()
        if icon.isNull():
            style = self.style()
            icon = style.standardIcon(QStyle.StandardPixmap.SP_DesktopIcon)
            if icon.isNull():
                icon = style.standardIcon(QStyle.StandardPixmap.SP_FileIcon)
        tray_icon.setIcon(icon)
        tray_icon.setToolTip("L Scheduler")

        tray_menu = QMenu(self)
        action_show = tray_menu.addAction("显示主窗口")
        action_quit = tray_menu.addAction("退出")
        action_show.triggered.connect(self.restore_from_tray)
        action_quit.triggered.connect(self._quit_from_tray)
        tray_icon.setContextMenu(tray_menu)
        tray_icon.activated.connect(self._on_tray_activated)
        tray_icon.show()
        self._tray_icon = tray_icon

    def _setup_timer(self) -> None:
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh_table)
        self._timer.start(self._refresh_interval_ms)

    def _show_table_context_menu(self, pos) -> None:
        """表格右键菜单：立即执行 / 启用​/​停用 / 任务设置。"""
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        # 确保右键同时选中该行
        self.table.selectRow(row)
        name_item = self.table.item(row, 0)
        self._selected_job_name = name_item.text() if name_item else None

        menu = QMenu(self)
        action_run = menu.addAction("立即执行")
        action_toggle = menu.addAction("启用/停用")
        menu.addSeparator()
        action_task_settings = menu.addAction("任务设置...")

        # 判断当前任务是否支持独立设置（来源为 .py 且同目录有 setting.py）
        job = self._scheduler.get_job(self._selected_job_name or "")
        source = getattr(job, "source", "") if job else ""
        has_setting = (
            source
            and Path(source).suffix.lower() == ".py"
            and (Path(source).resolve().parent / "setting.py").is_file()
        )
        action_task_settings.setEnabled(bool(has_setting))

        action = menu.exec(self.table.viewport().mapToGlobal(pos))
        if action == action_run:
            self.run_selected_job()
        elif action == action_toggle:
            self.toggle_selected_job()
        elif action == action_task_settings:
            self.open_task_settings()

    def _on_selection_changed(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            self._selected_job_name = None
            self.status_label.setText("状态: 未选择任务")
            return
        name_item = self.table.item(row, 0)
        self._selected_job_name = name_item.text() if name_item else None
        self.status_label.setText(f"状态: 已选择任务 {self._selected_job_name}")

    def refresh_table(self) -> None:
        rows = self._scheduler.status()
        self._refresh_count += 1

        # 仅当任务列表发生变化时重建下拉框，避免每秒重建带来的 Qt 开销
        current_job_names = [j.name for j in self._scheduler.list_jobs()]
        if current_job_names != self._last_job_names:
            self._last_job_names = current_job_names
            self._rebuild_log_task_combo()
        self.table.setRowCount(len(rows))
        for idx, row in enumerate(rows):
            is_running = row.get("running", False)
            if is_running:
                state_text = "▶ 运行中"
            elif row["enabled"]:
                state_text = "就绪"
            else:
                state_text = "停用"
            values = [
                row["name"],
                "是" if row["enabled"] else "否",
                row["schedule"],
                row["source"],
                str(row["run_count"]),
                str(row["error_count"]),
                str(row["last_run"] or "-"),
                row["next_run"],
                state_text,
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if is_running:
                    item.setBackground(Qt.GlobalColor.darkGreen)
                    item.setForeground(Qt.GlobalColor.white)
                self.table.setItem(idx, col, item)

        # 列宽计算很耗时：仅在首次或每 10 次刷新时执行一次
        if self._refresh_count == 1 or (self._refresh_count % 10 == 0):
            self.table.resizeColumnsToContents()
            self.table.setColumnWidth(3, min(self.table.columnWidth(3), 200))

    def _require_selected_job(self) -> Optional[str]:
        if not self._selected_job_name:
            QMessageBox.warning(self, "未选择任务", "请先在表格中选择一个任务。")
            return None
        return self._selected_job_name

    def run_selected_job(self) -> None:
        name = self._require_selected_job()
        if name is None:
            return

        job = self._scheduler.get_job(name)
        if job is None:
            QMessageBox.critical(self, "执行失败", f"找不到任务：{name}")
            return
        if job.is_running:
            QMessageBox.information(
                self, "任务运行中",
                f"任务 {name!r} 当前正在运行中，无需重复触发。\n"
                "请观察表格中的\u201c状态\u201d列。",
            )
            return

        prev_run_count = job.run_count
        prev_error_count = job.error_count

        ok = self._scheduler.trigger_job_once(name)
        if not ok:
            QMessageBox.critical(self, "触发失败", f"任务 {name!r} 触发失败，请稍后重试。")
            return

        self.status_label.setText(f"状态: 已触发 {name}，等待启动结果…")
        self.refresh_table()
        QTimer.singleShot(2500, lambda: self._check_run_result(name, prev_run_count, prev_error_count))

    def _check_run_result(self, name: str, prev_run_count: int, prev_error_count: int) -> None:
        """2.5 秒后检查任务启动结果并弹窗提示。"""
        job = self._scheduler.get_job(name)
        if job is None:
            return
        self.refresh_table()

        new_errors = job.error_count - prev_error_count
        new_runs = job.run_count - prev_run_count

        if job.is_running:
            QMessageBox.information(
                self,
                "✅ 启动成功",
                f"任务 {name!r} 已成功启动，当前正在运行中。\n"
                "表格中“状态”列显示 \u25b6 运行中 时表示进程仍在活跃。",
            )
            self.status_label.setText(f"状态: 任务 {name} 正在运行")
        elif new_errors > 0:
            QMessageBox.critical(
                self,
                "❌ 启动失败",
                f"任务 {name!r} 执行出错（+{new_errors} 次错误）。\n\n"
                "请切换到“日志”标签页查看详细错误输出。",
            )
            self.status_label.setText(f"状态: 任务 {name} 执行失败")
        elif new_runs > 0:
            QMessageBox.warning(
                self,
                "⚠️ 任务过快完成",
                f"任务 {name!r} 已执行完成，但在 2.5 秒内就退出了。\n\n"
                "如果期望任务长期运行（如文件监听），请检查：\n"
                "  • setting.yaml 中的同步目录 / 文件对是否已配置\n"
                "  • 切换到“日志”标签页查看具体输出",
            )
            self.status_label.setText(f"状态: 任务 {name} 已完成（请检查配置）")
        else:
            self.status_label.setText(f"状态: 任务 {name} 已触发，请稍后观察状态列")

    def toggle_selected_job(self) -> None:
        name = self._require_selected_job()
        if name is None:
            return

        job = self._scheduler.get_job(name)
        if job is None:
            QMessageBox.critical(self, "切换失败", f"任务不存在: {name}")
            return

        new_enabled = not job.enabled
        ok = self._scheduler.set_job_enabled(name, new_enabled)
        if not ok:
            QMessageBox.critical(self, "切换失败", f"任务状态更新失败: {name}")
            return
        self.status_label.setText(f"状态: 任务 {name} 已{'启用' if new_enabled else '停用'}")
        self.refresh_table()

    def open_task_settings(self) -> None:
        """打开当前选中 .py 任务同目录下 setting.py 的独立设置窗口。"""
        name = self._require_selected_job()
        if name is None:
            return

        job = self._scheduler.get_job(name)
        if job is None:
            QMessageBox.critical(self, "任务不存在", f"找不到任务: {name}")
            return

        source = getattr(job, "source", "") or ""
        if Path(source).suffix.lower() != ".py":
            QMessageBox.information(
                self,
                "不支持独立设置",
                f"任务 {name!r} 不是 .py 脚本，暂不支持独立设置窗口。",
            )
            return

        setting_path = Path(source).resolve().parent / "setting.py"
        if not setting_path.is_file():
            QMessageBox.information(
                self,
                "未找到设置模块",
                f"在任务目录下未找到 setting.py：\n{setting_path}",
            )
            return

        # 动态加载 setting.py
        try:
            spec = importlib.util.spec_from_file_location("_task_setting", str(setting_path))
            if spec is None or spec.loader is None:
                raise ImportError(f"无法解析模块: {setting_path}")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
        except Exception as exc:
            QMessageBox.critical(self, "加载设置模块失败", str(exc))
            return

        create_fn = getattr(mod, "create_settings_dialog", None)
        if create_fn is None:
            QMessageBox.critical(
                self,
                "接口缺失",
                f"setting.py 中未实现 create_settings_dialog()：\n{setting_path}",
            )
            return

        try:
            dialog = create_fn(parent=self)
        except Exception as exc:
            QMessageBox.critical(self, "创建设置窗口失败", str(exc))
            return

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        # 取新参数并写回 task_files.json
        get_args_fn = getattr(dialog, "get_arguments", None)
        if get_args_fn is not None:
            try:
                new_args: list[str] = get_args_fn()
                self._update_task_arguments(name, new_args)
            except Exception as exc:
                QMessageBox.warning(self, "参数写回失败", str(exc))

    def _update_task_arguments(self, task_name: str, arguments: list[str]) -> None:
        """更新 task_files.json 中指定任务的 arguments 并热重载。"""
        specs = load_task_file_specs(self._task_config_path)
        updated = False
        for spec in specs:
            if spec["name"] == task_name:
                spec["arguments"] = arguments
                updated = True
                break
        if not updated:
            return
        save_task_file_specs(self._task_config_path, specs)
        self._reload_tasks_from_config()

    def open_settings(self) -> None:
        dialog = TaskConfigDialog(config_path=self._task_config_path, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            new_specs = dialog.get_specs()
            save_task_file_specs(self._task_config_path, new_specs)
            self._reload_tasks_from_config()
        except TaskConfigError as exc:
            QMessageBox.critical(self, "设置保存失败", str(exc))
            return
        QMessageBox.information(self, "保存成功", "任务设置已保存并重载。")

    def _reload_tasks_from_config(self) -> None:
        task_specs = load_task_file_specs(self._task_config_path)
        self._scheduler.stop()
        for job in self._scheduler.list_jobs():
            self._scheduler.remove_job(job.name)
        register_file_tasks(self._scheduler, task_specs)
        if self._py_task_dir and self._py_task_dir.strip():
            py_task_specs = scan_py_task_dir(self._py_task_dir)
            register_file_tasks(self._scheduler, py_task_specs)
        self._scheduler.start(block=False)
        self.refresh_table()

    def restart_app(self) -> None:
        """停止调度器并重新启动程序。"""
        reply = QMessageBox.question(
            self,
            "确认重启",
            "确定要重启程序吗？\n调度器将停止，所有正在运行的任务将被中断。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._timer.stop()
        if self._tray_icon is not None:
            self._tray_icon.hide()
        self._scheduler.stop()
        subprocess.Popen([sys.executable] + sys.argv, close_fds=True)
        self._force_quit = True
        self.close()

    def minimize_to_tray(self) -> None:
        """按钮触发：最小化到通知栏。"""
        if self._tray_icon is None:
            QMessageBox.warning(self, "通知栏不可用", "当前系统不支持通知栏图标。")
            return
        self.hide()
        self._tray_icon.showMessage(
            "L Scheduler",
            "程序已最小化到通知栏，双击图标可恢复。",
            QSystemTrayIcon.MessageIcon.Information,
            2000,
        )
        self.status_label.setText("状态: 已最小化到通知栏")

    def restore_from_tray(self) -> None:
        """从通知栏恢复窗口。"""
        # Windows 上有时仅 showNormal/activateWindow 不会真正把窗口“恢复到可交互前台”，
        # 这里做更强的恢复：清掉最小化状态、显示、置顶、激活。
        try:
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        except Exception:
            pass
        self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.restore_from_tray()

    def _quit_from_tray(self) -> None:
        if self._tray_icon is not None:
            self._tray_icon.hide()
        self._force_quit = True
        self.close()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        # 点击窗口关闭按钮时，默认最小化到通知栏，避免误停调度器。
        # 只有通过通知栏菜单「退出」才真正关闭并停止 scheduler。
        if not self._force_quit and self._tray_icon is not None:
            event.ignore()
            self.minimize_to_tray()
            return

        self._timer.stop()
        if self._tray_icon is not None:
            self._tray_icon.hide()
        self._scheduler.stop()
        event.accept()


def run_scheduler_ui(
    scheduler: Scheduler,
    task_config_path: str,
    instance_tag: str = "",
    log_file: str = "logs/l_scheduler.log",
    py_task_dir: str = "",
) -> int:
    """启动并阻塞运行 UI。"""
    # On Windows, set explicit AppUserModelID so taskbar icon is stable and grouped correctly.
    if sys.platform.startswith("win"):
        app_id = "Lugwit.l_scheduler"
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        except Exception:
            # Non-fatal: UI can still run without explicit taskbar AppID.
            pass

    inst = QApplication.instance()
    app = inst if isinstance(inst, QApplication) else QApplication(sys.argv)
    app_icon = _resolve_scheduler_icon(app)
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)
    window = SchedulerWindow(
        scheduler=scheduler,
        task_config_path=task_config_path,
        instance_tag=instance_tag,
        app_icon=app_icon,
        log_file=log_file,
        py_task_dir=py_task_dir,
    )
    window.show()
    return app.exec()

