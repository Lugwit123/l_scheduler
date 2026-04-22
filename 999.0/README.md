## l_scheduler

本包提供一个本地常驻的定时任务调度器（类 cron），支持：
- CLI：`l_scheduler --status / --daemon / --ui`
- 任务配置：`src/l_scheduler/config/task_files.json`
- 任务类型：`.bat` / `.exe` / `.py`
- UI：PySide6（可选；缺失时 `--ui` 会给出明确报错）

### 启动

- **UI 模式**

```bash
l_scheduler --ui
```

- **后台模式**

```bash
l_scheduler --daemon
```

- **查看状态**

```bash
l_scheduler --status
```

### 配置文件 `task_files.json`

根结构：

```json
{ "tasks": [ ... ] }
```

单个任务字段（常用）：
- `name`：任务名（唯一、非空）
- `path`：任务路径（`.bat/.exe/.py`）
- `enabled`：是否启用
- `arguments`：参数数组（可省略）
- `working_directory`：工作目录（可省略）
- `success_return_codes`：成功返回码数组（默认 `[0]`）
- `schedule`：定时结构
  - interval：`{ "type": "interval", "seconds": 60 }`
  - daily：`{ "type": "daily", "at": "09:00" }`

外部日志（可选，用于 UI 直接打开任务生成的日志）：
- `external_log_file`：外部日志文件路径
- `external_log_env_var`：环境变量名（仅当任务需要通过环境变量接收日志路径时使用）

### 路径解析规则

配置中的 `path` / `working_directory` / `external_log_file` 支持：
- **环境变量展开**：`%VAR%` / `$VAR` / `${VAR}`
- **相对路径**：相对 `task_files.json` 所在目录

### UI 依赖

`--ui` 需要 `PySide6`。如果当前 Python 运行时缺少 PySide6，会提示安装方式。

### 示例 Python 任务依赖

`py_task/watchdog_bidirectional_sync.py` 依赖 `watchdog`，需手动安装：

```bash
pip install watchdog
```

