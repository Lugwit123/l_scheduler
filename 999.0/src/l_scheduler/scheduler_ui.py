# -*- coding: utf-8 -*-
"""l_scheduler 的 PySide6 管理界面。"""
from __future__ import annotations

import re
import sys
import ctypes
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QMenu,
    QStyle,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from l_scheduler.scheduler import Scheduler
from l_scheduler.tasks import (
    TaskConfigError,
    TaskFileSpec,
    load_task_file_specs,
    register_file_tasks,
    save_task_file_specs,
)


def _resolve_scheduler_icon(app: QApplication) -> QIcon:
    """Resolve a stable icon for app/window/tray on Windows."""
    candidates = [
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

        self.table = QTableWidget(0, 8)
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
            ]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(1, 260)
        self.table.setColumnWidth(7, 220)
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
            return ["", "", "interval", "60", "true", "", "0", ""]
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

            name = (name_item.text() if name_item is not None else "").strip()
            path = (path_item.text() if path_item is not None else "").strip()
            schedule_type = (schedule_type_item.text() if schedule_type_item is not None else "interval").strip().lower()
            schedule_value = (schedule_value_item.text() if schedule_value_item is not None else "").strip()
            enabled_raw = (enabled_item.text() if enabled_item is not None else "true").strip().lower()
            args_text = (args_item.text() if args_item is not None else "").strip()
            success_codes_text = (success_codes_item.text() if success_codes_item is not None else "0").strip()
            working_directory = (working_directory_item.text() if working_directory_item is not None else "").strip()

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


class SchedulerWindow(QMainWindow):
    """调度器管理窗口。"""

    def __init__(
        self,
        scheduler: Scheduler,
        task_config_path: str,
        instance_tag: str = "",
        refresh_interval_ms: int = 1000,
        app_icon: Optional[QIcon] = None,
    ) -> None:
        super().__init__()
        self._scheduler = scheduler
        self._task_config_path = task_config_path
        self._instance_tag = instance_tag
        self._refresh_interval_ms = refresh_interval_ms
        self._selected_job_name: Optional[str] = None
        self._tray_icon: Optional[QSystemTrayIcon] = None

        title = "L Scheduler - 任务管理"
        if self._instance_tag:
            title = f"{title} [{self._instance_tag}]"
        self.setWindowTitle(title)
        if app_icon is not None and not app_icon.isNull():
            self.setWindowIcon(app_icon)
        self.resize(980, 520)
        self._build_ui()
        self._setup_tray_icon()
        self._setup_timer()
        self.refresh_table()

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        self.setCentralWidget(root)

        title = QLabel("定时任务管理面板")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["任务名", "启用", "定时", "来源", "执行次数", "失败次数", "最近执行", "下次执行"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(3, 200)
        layout.addWidget(self.table)

        btn_layout = QHBoxLayout()
        self.btn_refresh = QPushButton("刷新")
        self.btn_run = QPushButton("立即执行")
        self.btn_toggle = QPushButton("启用/停用")
        self.btn_settings = QPushButton("设置")
        self.btn_minimize_to_tray = QPushButton("最小化到通知栏")
        btn_layout.addWidget(self.btn_refresh)
        btn_layout.addWidget(self.btn_run)
        btn_layout.addWidget(self.btn_toggle)
        btn_layout.addWidget(self.btn_settings)
        btn_layout.addWidget(self.btn_minimize_to_tray)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        self.status_label = QLabel("状态: 未选择任务")
        layout.addWidget(self.status_label)

        self.btn_refresh.clicked.connect(self.refresh_table)
        self.btn_run.clicked.connect(self.run_selected_job)
        self.btn_toggle.clicked.connect(self.toggle_selected_job)
        self.btn_settings.clicked.connect(self.open_settings)
        self.btn_minimize_to_tray.clicked.connect(self.minimize_to_tray)

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
        self.table.setRowCount(len(rows))
        for idx, row in enumerate(rows):
            values = [
                row["name"],
                "是" if row["enabled"] else "否",
                row["schedule"],
                row["source"],
                str(row["run_count"]),
                str(row["error_count"]),
                str(row["last_run"] or "-"),
                row["next_run"],
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(idx, col, item)

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
        ok = self._scheduler.trigger_job_once(name)
        if not ok:
            QMessageBox.critical(self, "执行失败", f"任务不存在: {name}")
            return
        self.status_label.setText(f"状态: 已手动触发任务 {name}")
        self.refresh_table()

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
        self._scheduler.start(block=False)
        self.refresh_table()

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
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.restore_from_tray()

    def _quit_from_tray(self) -> None:
        if self._tray_icon is not None:
            self._tray_icon.hide()
        self.close()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._timer.stop()
        if self._tray_icon is not None:
            self._tray_icon.hide()
        self._scheduler.stop()
        event.accept()


def run_scheduler_ui(scheduler: Scheduler, task_config_path: str, instance_tag: str = "") -> int:
    """启动并阻塞运行 UI。"""
    # On Windows, set explicit AppUserModelID so taskbar icon is stable and grouped correctly.
    if sys.platform.startswith("win"):
        app_id = "Lugwit.l_scheduler"
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        except Exception:
            # Non-fatal: UI can still run without explicit taskbar AppID.
            pass

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    app_icon = _resolve_scheduler_icon(app)
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)
    window = SchedulerWindow(
        scheduler=scheduler,
        task_config_path=task_config_path,
        instance_tag=instance_tag,
        app_icon=app_icon,
    )
    window.show()
    return app.exec()

