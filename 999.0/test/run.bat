@echo off
REM 启动 l_scheduler 定时任务调度器
set "WUWO=D:\TD_Depot\Software\Lugwit_syncPlug\lugwit_insapp\trayapp\wuwo\wuwo.bat"
set "TASK_CONFIG=D:\TD_Depot\Software\Lugwit_syncPlug\lugwit_insapp\trayapp\rez-package-source\l_scheduler\999.0\src\l_scheduler\config\task_files.json"
set "LOG_FILE=%~dp0logs\l_scheduler.log"
set "L_SCHEDULER_TASK_FILES_CONFIG=%TASK_CONFIG%"
set "L_SCHEDULER_LOG_FILE=%LOG_FILE%"
call "%WUWO%" rez env python-3.12 Lugwit_Module l_scheduler  -- l_scheduler --ui %*
