# -*- coding: utf-8 -*-
"""
task1 任务综合设置窗口。

功能：
  - 双向目录同步（watchdog_bidirectional_sync.py）
  - 单文件同步（file_sync.py）

约定：
- 配置文件为同目录的 setting.yaml
- 对外暴露 create_settings_dialog(parent=None) -> QDialog
- 对话框 Accepted 后可调用 dialog.get_arguments() 取得双向同步 CLI 参数列表
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Optional

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QDoubleSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt

_SETTING_FILE = Path(__file__).resolve().parent / "setting.yaml"

# ── 默认配置（与 setting.yaml 结构完全对应） ──────────────────────────────
_DEFAULTS: dict[str, Any] = {
    "file_sync": {
        "pairs": [],
        "copy_on_start": True,
    },
    "sync": {"left": "", "right": ""},
    "behavior": {
        "initial_sync": True,
        "propagate_delete": False,
        "suppress_ttl_seconds": 1.5,
        "delete_confirm_delay_seconds": 3.0,
        "ignore_suffixes": ".tmp,.swp,.swx,.log,.cache,.bak",
    },
    "ready_check": {"retries": 5, "interval_seconds": 1.0},
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


# ── I/O ────────────────────────────────────────────────────────────────────

def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并，override 覆盖 base，缺失键用 base 补齐。"""
    result = copy.deepcopy(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def load_settings() -> dict:
    """读取 setting.yaml，缺失键用默认值补齐。"""
    if yaml is None:
        return copy.deepcopy(_DEFAULTS)
    if _SETTING_FILE.exists():
        try:
            raw = yaml.safe_load(_SETTING_FILE.read_text(encoding="utf-8")) or {}
            if isinstance(raw, dict):
                return _deep_merge(_DEFAULTS, raw)
        except Exception:
            pass
    return copy.deepcopy(_DEFAULTS)


def save_settings(cfg: dict) -> None:
    """将配置写回 setting.yaml（保留注释模板，仅更新值部分）。"""
    if yaml is None:
        raise RuntimeError("保存配置需要安装 PyYAML：pip install pyyaml")
    _SETTING_FILE.parent.mkdir(parents=True, exist_ok=True)
    # 重新生成完整 yaml，不保留注释（注释由模板文件维护）
    _SETTING_FILE.write_text(
        yaml.dump(cfg, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )


# ── 设置对话框 ─────────────────────────────────────────────────────────────

class SyncSettingsDialog(QDialog):
    """task1 综合设置对话框（双向目录同步 + 单文件同步）。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("同步任务设置")
        self.resize(720, 560)
        self._cfg: dict = load_settings()
        self._build_ui()
        self._populate()

    # ── UI 构建 ────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)

        tabs = QTabWidget()
        tabs.addTab(self._build_file_sync_tab(), "文件同步")
        tabs.addTab(self._build_sync_tab(), "目录同步")
        tabs.addTab(self._build_behavior_tab(), "行为控制")
        tabs.addTab(self._build_notify_tab(), "报错通知")
        root.addWidget(tabs)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ── Tab 0：单文件同步 ────────────────────────────────────────────

    def _build_file_sync_tab(self) -> QWidget:
        w = QWidget()
        root = QVBoxLayout(w)
        root.setSpacing(8)
        root.setContentsMargins(12, 12, 12, 12)

        root.addWidget(QLabel('监听指定文件，发生变化时自动将源文件复制到目标。'))

        # 匹配对表格
        self._file_pair_table = QTableWidget(0, 2)
        self._file_pair_table.setHorizontalHeaderLabels(["源文件", "目标文件"])
        self._file_pair_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._file_pair_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._file_pair_table.setAlternatingRowColors(True)
        self._file_pair_table.itemChanged.connect(self._on_file_pair_item_changed)
        root.addWidget(self._file_pair_table)
        
        self._ensure_empty_rows()

        # 操作按钮
        btn_row = QHBoxLayout()
        btn_add = QPushButton("➕  添加匹配")
        btn_add.clicked.connect(self._add_file_pair)
        btn_remove = QPushButton("➖  删除所选")
        btn_remove.clicked.connect(self._remove_file_pair)
        btn_row.addWidget(btn_add)
        btn_row.addWidget(btn_remove)
        btn_row.addStretch()
        root.addLayout(btn_row)

        # 制选项
        chk_row = QHBoxLayout()
        self._copy_on_start_chk = QCheckBox("启动时先做一次全量复制")
        chk_row.addWidget(self._copy_on_start_chk)
        chk_row.addStretch()
        root.addLayout(chk_row)

        return w

    def _ensure_empty_rows(self, min_empty: int = 2) -> None:
        """确保表格末尾至少有指定数量的空行。"""
        empty_count = 0
        for r in range(self._file_pair_table.rowCount() - 1, -1, -1):
            src_item = self._file_pair_table.item(r, 0)
            dst_item = self._file_pair_table.item(r, 1)
            src = src_item.text().strip() if src_item else ""
            dst = dst_item.text().strip() if dst_item else ""
            if not src and not dst:
                empty_count += 1
            else:
                break
        
        for _ in range(min_empty - empty_count):
            row = self._file_pair_table.rowCount()
            self._file_pair_table.insertRow(row)
            self._file_pair_table.setItem(row, 0, QTableWidgetItem(""))
            self._file_pair_table.setItem(row, 1, QTableWidgetItem(""))
    
    def _on_file_pair_item_changed(self, item: QTableWidgetItem) -> None:
        """当表格项内容改变时，检查是否需要添加新的空行。"""
        row = item.row()
        if row == self._file_pair_table.rowCount() - 1:
            src_item = self._file_pair_table.item(row, 0)
            dst_item = self._file_pair_table.item(row, 1)
            src = src_item.text().strip() if src_item else ""
            dst = dst_item.text().strip() if dst_item else ""
            if src or dst:
                self._ensure_empty_rows()
    
    def _add_file_pair(self) -> None:
        """弹出文件选择对话框添加一行。"""
        src, _ = QFileDialog.getOpenFileName(self, "选择源文件", str(Path.home()))
        if not src:
            return
        dst, _ = QFileDialog.getSaveFileName(self, "选择目标文件", src)
        if not dst:
            return
        
        for r in range(self._file_pair_table.rowCount()):
            src_item = self._file_pair_table.item(r, 0)
            dst_item = self._file_pair_table.item(r, 1)
            src_text = src_item.text().strip() if src_item else ""
            dst_text = dst_item.text().strip() if dst_item else ""
            if not src_text and not dst_text:
                self._file_pair_table.setItem(r, 0, QTableWidgetItem(src))
                self._file_pair_table.setItem(r, 1, QTableWidgetItem(dst))
                self._ensure_empty_rows()
                return
        
        row = self._file_pair_table.rowCount()
        self._file_pair_table.insertRow(row)
        self._file_pair_table.setItem(row, 0, QTableWidgetItem(src))
        self._file_pair_table.setItem(row, 1, QTableWidgetItem(dst))
        self._ensure_empty_rows()

    def _remove_file_pair(self) -> None:
        """删除已选行。"""
        rows = sorted({idx.row() for idx in self._file_pair_table.selectedIndexes()}, reverse=True)
        for r in rows:
            self._file_pair_table.removeRow(r)

    # ── Tab 1：同步目录 ────────────────────────────────────────────────────

    def _build_sync_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setSpacing(10)
        form.setContentsMargins(12, 12, 12, 12)

        self._left_edit = QLineEdit()
        self._left_edit.setPlaceholderText("源目录（左侧），必填")
        form.addRow("源目录（左）:", self._make_path_row(self._left_edit))

        self._right_edit = QLineEdit()
        self._right_edit.setPlaceholderText("目标目录（右侧），必填")
        form.addRow("目标目录（右）:", self._make_path_row(self._right_edit))

        return w

    # ── Tab 2：行为控制 ────────────────────────────────────────────────────

    def _build_behavior_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setSpacing(10)
        form.setContentsMargins(12, 12, 12, 12)

        self._initial_sync_chk = QCheckBox("启动时先做一次双向收敛")
        form.addRow("初始同步:", self._initial_sync_chk)

        self._propagate_delete_chk = QCheckBox("同步删除操作（谨慎开启）")
        form.addRow("删除同步:", self._propagate_delete_chk)

        self._suppress_ttl_spin = QDoubleSpinBox()
        self._suppress_ttl_spin.setRange(0.1, 60.0)
        self._suppress_ttl_spin.setSingleStep(0.5)
        self._suppress_ttl_spin.setSuffix(" 秒")
        self._suppress_ttl_spin.setToolTip("写回事件抑制窗口，防止来回复制")
        form.addRow("抑制窗口:", self._suppress_ttl_spin)

        self._delete_delay_spin = QDoubleSpinBox()
        self._delete_delay_spin.setRange(0.5, 60.0)
        self._delete_delay_spin.setSingleStep(0.5)
        self._delete_delay_spin.setSuffix(" 秒")
        self._delete_delay_spin.setToolTip("删除确认延迟，避免网络波动误删")
        form.addRow("删除确认延迟:", self._delete_delay_spin)

        self._ignore_suffixes_edit = QLineEdit()
        self._ignore_suffixes_edit.setPlaceholderText(".tmp,.swp,.log")
        self._ignore_suffixes_edit.setToolTip("逗号分隔，不区分大小写")
        form.addRow("忽略后缀:", self._ignore_suffixes_edit)

        self._ready_retries_spin = QSpinBox()
        self._ready_retries_spin.setRange(1, 30)
        self._ready_retries_spin.setToolTip("目录可访问性检查重试次数")
        form.addRow("检查重试次数:", self._ready_retries_spin)

        self._ready_interval_spin = QDoubleSpinBox()
        self._ready_interval_spin.setRange(0.5, 30.0)
        self._ready_interval_spin.setSingleStep(0.5)
        self._ready_interval_spin.setSuffix(" 秒")
        self._ready_interval_spin.setToolTip("每次重试间隔")
        form.addRow("检查重试间隔:", self._ready_interval_spin)

        return w

    # ── Tab 3：报错通知 ────────────────────────────────────────────────────

    def _build_notify_tab(self) -> QWidget:
        w = QWidget()
        root = QVBoxLayout(w)
        root.setSpacing(8)
        root.setContentsMargins(12, 12, 12, 12)

        self._notify_enabled_chk = QCheckBox("启用报错通知（总开关）")
        root.addWidget(self._notify_enabled_chk)

        # ── 桌面通知 ──
        desktop_box = QGroupBox("桌面气泡通知")
        dl = QVBoxLayout(desktop_box)
        self._desktop_enabled_chk = QCheckBox("启用（需安装 plyer 或 win10toast）")
        dl.addWidget(self._desktop_enabled_chk)
        root.addWidget(desktop_box)

        # ── 邮件通知 ──
        email_box = QGroupBox("邮件通知")
        ef = QFormLayout(email_box)
        ef.setSpacing(6)
        self._email_enabled_chk = QCheckBox("启用")
        ef.addRow("", self._email_enabled_chk)
        self._smtp_host_edit = QLineEdit()
        ef.addRow("SMTP 地址:", self._smtp_host_edit)
        self._smtp_port_spin = QSpinBox()
        self._smtp_port_spin.setRange(1, 65535)
        ef.addRow("SMTP 端口:", self._smtp_port_spin)
        self._smtp_ssl_chk = QCheckBox("SSL")
        ef.addRow("", self._smtp_ssl_chk)
        self._email_user_edit = QLineEdit()
        ef.addRow("用户名:", self._email_user_edit)
        self._email_pass_edit = QLineEdit()
        self._email_pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        ef.addRow("密码:", self._email_pass_edit)
        self._email_from_edit = QLineEdit()
        ef.addRow("发件人:", self._email_from_edit)
        self._email_to_edit = QLineEdit()
        self._email_to_edit.setPlaceholderText("多个收件人用逗号分隔")
        ef.addRow("收件人:", self._email_to_edit)
        root.addWidget(email_box)

        # ── Webhook 通知 ──
        wh_box = QGroupBox("HTTP Webhook（企业微信 / 钉钉 / 飞书 / 自定义）")
        wf = QFormLayout(wh_box)
        wf.setSpacing(6)
        self._wh_enabled_chk = QCheckBox("启用")
        wf.addRow("", self._wh_enabled_chk)
        self._wh_url_edit = QLineEdit()
        self._wh_url_edit.setPlaceholderText("https://...")
        wf.addRow("URL:", self._wh_url_edit)
        self._wh_body_edit = QTextEdit()
        self._wh_body_edit.setFixedHeight(60)
        self._wh_body_edit.setPlaceholderText('{message} 会被替换为错误信息')
        wf.addRow("Body 模板:", self._wh_body_edit)
        self._wh_headers_edit = QTextEdit()
        self._wh_headers_edit.setFixedHeight(50)
        self._wh_headers_edit.setPlaceholderText("Key: Value（每行一条）")
        wf.addRow("请求头:", self._wh_headers_edit)
        root.addWidget(wh_box)

        root.addStretch()
        return w

    # ── 辅助 ───────────────────────────────────────────────────────────────

    def _make_path_row(self, edit: QLineEdit, is_file: bool = False) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(edit)
        btn = QPushButton("浏览...")
        btn.setFixedWidth(70)
        if is_file:
            btn.clicked.connect(lambda: self._browse_file(edit))
        else:
            btn.clicked.connect(lambda: self._browse_dir(edit))
        layout.addWidget(btn)
        return container

    def _browse_file(self, edit: QLineEdit) -> None:
        current = edit.text().strip() or str(Path.home())
        chosen, _ = QFileDialog.getOpenFileName(self, "选择文件", current)
        if chosen:
            edit.setText(chosen)

    def _browse_dir(self, edit: QLineEdit) -> None:
        current = edit.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "选择目录", current)
        if chosen:
            edit.setText(chosen)

    # ── 填充 / 收集 ────────────────────────────────────────────────────────

    def _populate(self) -> None:
        c = self._cfg

        # 单文件同步
        fs = c.get("file_sync", {})
        pairs = fs.get("pairs", []) or []
        self._file_pair_table.setRowCount(0)
        for pair in pairs:
            if not isinstance(pair, dict):
                continue
            row = self._file_pair_table.rowCount()
            self._file_pair_table.insertRow(row)
            self._file_pair_table.setItem(row, 0, QTableWidgetItem(str(pair.get("src", ""))))
            self._file_pair_table.setItem(row, 1, QTableWidgetItem(str(pair.get("dst", ""))))
        self._copy_on_start_chk.setChecked(bool(fs.get("copy_on_start", True)))
        
        self._ensure_empty_rows()

        # 双向目录同步
        s = c.get("sync", {})
        self._left_edit.setText(s.get("left", ""))
        self._right_edit.setText(s.get("right", ""))

        b = c.get("behavior", {})
        self._initial_sync_chk.setChecked(bool(b.get("initial_sync", True)))
        self._propagate_delete_chk.setChecked(bool(b.get("propagate_delete", False)))
        self._suppress_ttl_spin.setValue(float(b.get("suppress_ttl_seconds", 1.5)))
        self._delete_delay_spin.setValue(float(b.get("delete_confirm_delay_seconds", 3.0)))
        self._ignore_suffixes_edit.setText(str(b.get("ignore_suffixes", "")))

        rc = c.get("ready_check", {})
        self._ready_retries_spin.setValue(int(rc.get("retries", 5)))
        self._ready_interval_spin.setValue(float(rc.get("interval_seconds", 1.0)))

        n = c.get("error_notify", {})
        self._notify_enabled_chk.setChecked(bool(n.get("enabled", False)))
        self._desktop_enabled_chk.setChecked(bool(n.get("desktop", {}).get("enabled", True)))

        em = n.get("email", {})
        self._email_enabled_chk.setChecked(bool(em.get("enabled", False)))
        self._smtp_host_edit.setText(str(em.get("smtp_host", "")))
        self._smtp_port_spin.setValue(int(em.get("smtp_port", 465)))
        self._smtp_ssl_chk.setChecked(bool(em.get("smtp_ssl", True)))
        self._email_user_edit.setText(str(em.get("username", "")))
        self._email_pass_edit.setText(str(em.get("password", "")))
        self._email_from_edit.setText(str(em.get("from_addr", "")))
        self._email_to_edit.setText(str(em.get("to_addrs", "")))

        wh = n.get("webhook", {})
        self._wh_enabled_chk.setChecked(bool(wh.get("enabled", False)))
        self._wh_url_edit.setText(str(wh.get("url", "")))
        self._wh_body_edit.setPlainText(str(wh.get("body_template", "")))
        self._wh_headers_edit.setPlainText(str(wh.get("headers", "")))

    def _collect(self) -> dict:
        # 单文件同步
        pairs: list[dict] = []
        for r in range(self._file_pair_table.rowCount()):
            src_item = self._file_pair_table.item(r, 0)
            dst_item = self._file_pair_table.item(r, 1)
            src = src_item.text().strip() if src_item else ""
            dst = dst_item.text().strip() if dst_item else ""
            if src and dst:
                pairs.append({"src": src, "dst": dst})

        return {
            "file_sync": {
                "pairs": pairs,
                "copy_on_start": self._copy_on_start_chk.isChecked(),
            },
            "sync": {
                "left": self._left_edit.text().strip(),
                "right": self._right_edit.text().strip(),
            },
            "behavior": {
                "initial_sync": self._initial_sync_chk.isChecked(),
                "propagate_delete": self._propagate_delete_chk.isChecked(),
                "suppress_ttl_seconds": self._suppress_ttl_spin.value(),
                "delete_confirm_delay_seconds": self._delete_delay_spin.value(),
                "ignore_suffixes": self._ignore_suffixes_edit.text().strip(),
            },
            "ready_check": {
                "retries": self._ready_retries_spin.value(),
                "interval_seconds": self._ready_interval_spin.value(),
            },
            "error_notify": {
                "enabled": self._notify_enabled_chk.isChecked(),
                "desktop": {"enabled": self._desktop_enabled_chk.isChecked()},
                "email": {
                    "enabled": self._email_enabled_chk.isChecked(),
                    "smtp_host": self._smtp_host_edit.text().strip(),
                    "smtp_port": self._smtp_port_spin.value(),
                    "smtp_ssl": self._smtp_ssl_chk.isChecked(),
                    "username": self._email_user_edit.text().strip(),
                    "password": self._email_pass_edit.text(),
                    "from_addr": self._email_from_edit.text().strip(),
                    "to_addrs": self._email_to_edit.text().strip(),
                },
                "webhook": {
                    "enabled": self._wh_enabled_chk.isChecked(),
                    "url": self._wh_url_edit.text().strip(),
                    "body_template": self._wh_body_edit.toPlainText(),
                    "headers": self._wh_headers_edit.toPlainText(),
                },
            },
        }

    # ── 确认 ───────────────────────────────────────────────────────────────

    def _on_accept(self) -> None:
        cfg = self._collect()
        has_dir_sync = cfg["sync"]["left"] and cfg["sync"]["right"]
        has_file_sync = bool(cfg["file_sync"]["pairs"])
        if not has_dir_sync and not has_file_sync:
            QMessageBox.warning(
                self,
                "配置不完整",
                "请至少配置一种同步：\n• 目录同步：填写源目录和目标目录\n• 文件同步：添加至少一条文件匹配",
            )
            return
        try:
            save_settings(cfg)
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        self._cfg = cfg
        self.accept()

    # ── 公开接口 ───────────────────────────────────────────────────────────

    def get_arguments(self) -> list[str]:
        """返回 watchdog_bidirectional_sync.py 的 CLI 参数列表（从已保存配置读取）。"""
        cfg = load_settings()
        s = cfg.get("sync", {})
        b = cfg.get("behavior", {})
        rc = cfg.get("ready_check", {})
        args: list[str] = []
        if s.get("left"):
            args += ["--left", s["left"]]
        if s.get("right"):
            args += ["--right", s["right"]]
        if not b.get("initial_sync", True):
            args.append("--no-initial-sync")
        if b.get("propagate_delete", False):
            args.append("--propagate-delete")
        args += ["--suppress-ttl-seconds", str(b.get("suppress_ttl_seconds", 1.5))]
        args += ["--delete-confirm-delay-seconds", str(b.get("delete_confirm_delay_seconds", 3.0))]
        if b.get("ignore_suffixes"):
            args += ["--ignore-suffixes", b["ignore_suffixes"]]
        args += ["--ready-check-retries", str(rc.get("retries", 5))]
        args += ["--ready-check-interval-seconds", str(rc.get("interval_seconds", 1.0))]
        return args


# ── 约定入口 ───────────────────────────────────────────────────────────────

def create_settings_dialog(parent: Optional[QWidget] = None) -> QDialog:
    """l_scheduler 约定入口：构造并返回设置对话框实例。"""
    if yaml is None:
        QMessageBox.critical(
            parent,  # type: ignore[arg-type]
            "缺少依赖",
            "设置窗口需要 PyYAML，请先安装：\npip install pyyaml",
        )
        # 返回一个立即 reject 的空 dialog，避免上层崩溃
        dlg = QDialog(parent)
        dlg.reject()
        return dlg
    return SyncSettingsDialog(parent=parent)
