@echo off
REM 启动 l_scheduler 定时任务调度器
setlocal EnableExtensions

REM 本脚本位于: ...\rez-package-source\l_scheduler\999.0\test\run.bat
set "PKG_ROOT=%~dp0.."
for %%I in ("%PKG_ROOT%") do set "PKG_ROOT=%%~fI"

REM 允许外部覆盖 WUWO（例如 set WUWO=...\wuwo.bat）
if not defined WUWO (
  REM test -> 999.0 -> l_scheduler -> rez-package-source -> trayapp
  set "WUWO=%PKG_ROOT%\..\..\..\..\wuwo\wuwo.bat"
)
for %%I in ("%WUWO%") do set "WUWO=%%~fI"

set "TASK_CONFIG=%PKG_ROOT%\src\l_scheduler\config\task_files.json"
for %%I in ("%TASK_CONFIG%") do set "TASK_CONFIG=%%~fI"

set "LOG_FILE=%~dp0logs\l_scheduler.log"
for %%I in ("%LOG_FILE%") do set "LOG_FILE=%%~fI"

if not exist "%WUWO%" (
  echo ERROR: WUWO not found: "%WUWO%"
  exit /b 1
)
if not exist "%TASK_CONFIG%" (
  echo ERROR: task config not found: "%TASK_CONFIG%"
  exit /b 1
)

set "L_SCHEDULER_TASK_FILES_CONFIG=%TASK_CONFIG%"
set "L_SCHEDULER_LOG_FILE=%LOG_FILE%"
call "%WUWO%" rez env python-3.12 Lugwit_Module l_scheduler -- l_scheduler --ui %*
endlocal
