@echo off
setlocal EnableExtensions EnableDelayedExpansion
set "SOURCE=D:\TD_Depot\Wuzu_dev\anim_upload_muse_tool\src\anim_upload_muse_tool\samba_to_muse"
set "DEST=L:\temp\j_muse_backup\samba_to_muse"
set "SOURCE_LUGWIT_MODULE=D:\TD_Depot\Software\Lugwit_syncPlug\lugwit_insapp\trayapp\rez-package-source\Lugwit_Module\999.0\src\Lugwit_Module"
set "DEST_LUGWIT_MODULE=D:\TD_Depot\Wuzu_dev\anim_upload_muse_tool\src\anim_upload_muse_tool\@baselibs\Lugwit_Module"
set "SOURCE_PYTRACEMP=D:\TD_Depot\Software\Lugwit_syncPlug\lugwit_insapp\trayapp\rez-package-source\pytracemp\999.0\src\pytracemp"
set "DEST_PYTRACEMP=D:\TD_Depot\Wuzu_dev\anim_upload_muse_tool\src\anim_upload_muse_tool\@baselibs\pytracemp"
set "SOURCE_ANIM_UPLOAD_MUSE_TOOL=D:\TD_Depot\Wuzu_dev\anim_upload_muse_tool\src\anim_upload_muse_tool"
set "DEST_ANIM_UPLOAD_MUSE_TOOL=P:\packages\anim_upload_muse_tool\0.2.3\src\anim_upload_muse_tool"
:: 日志路径：优先使用外部注入的 %LOG_FILE%（例如由 l_scheduler 注入），否则使用默认路径
if not defined LOG_FILE (
    set "LOG_DIR=D:\TD_Depot\Software\lnk\logs"
    set "LOG_FILE=%LOG_DIR%\sync_samba_to_muse.log"
) else (
    for %%I in ("%LOG_FILE%") do set "LOG_DIR=%%~dpI"
)

:: 替换时间中的空格为0
set "LOG_FILE=%LOG_FILE: =0%"

:: 创建日志目录（如果不存在）
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

:: 日志轮转：主日志超过 2MB 则改名备份，重新写新日志
set "MAX_LOG_BYTES=2097152"
if exist "%LOG_FILE%" (
    for %%I in ("%LOG_FILE%") do set "LOG_SIZE=%%~zI"
    if defined LOG_SIZE if !LOG_SIZE! GEQ !MAX_LOG_BYTES! (
        set "TS=%date:~0,4%%date:~5,2%%date:~8,2%_%time:~0,2%%time:~3,2%%time:~6,2%"
        set "TS=!TS: =0!"
        ren "%LOG_FILE%" "sync_samba_to_muse_!TS!.log" 2>nul
    )
)

echo ==================================================>>"%LOG_FILE%"
echo [%date% %time%] Start one-way sync (source to dest)>>"%LOG_FILE%"
echo ==================================================>>"%LOG_FILE%"

:: robocopy 多线程：默认 4 线程，可通过环境变量 ROBOCOPY_MT 覆盖（例如 8）
if not defined ROBOCOPY_MT set "ROBOCOPY_MT=4"
set "ROBOCOPY_MT_OPT=/MT:%ROBOCOPY_MT%"

call :sync_oneway "%SOURCE%" "%DEST%" "samba_to_muse"
call :sync_oneway "%SOURCE_LUGWIT_MODULE%" "%DEST_LUGWIT_MODULE%" "Lugwit_Module"
call :sync_oneway "%SOURCE_PYTRACEMP%" "%DEST_PYTRACEMP%" "pytracemp"
@REM call :sync_oneway "%SOURCE_ANIM_UPLOAD_MUSE_TOOL%" "%DEST_ANIM_UPLOAD_MUSE_TOOL%" "anim_upload_muse_tool"

:: 清理旧日志，只保留最新的10份
cd /d "%LOG_DIR%"
for /f "skip=10 delims=" %%a in ('dir /b /o-d sync_samba_to_muse_*.log 2^>nul') do del /q "%%a" 2>nul
goto :eof

:sync_oneway
set "SRC=%~1"
set "DST=%~2"
set "NAME=%~3"

echo.>>"%LOG_FILE%"
echo ---------- [%NAME%] %SRC% to %DST% ---------->>"%LOG_FILE%"
echo [CHECK][%NAME%] SRC=%SRC%
echo [CHECK][%NAME%] SRC=%SRC%>>"%LOG_FILE%"
if exist "%SRC%" (
    echo [CHECK][%NAME%] source exists: YES
    echo [CHECK][%NAME%] source exists: YES>>"%LOG_FILE%"
) else (
    echo [CHECK][%NAME%] source exists: NO
    echo [CHECK][%NAME%] source exists: NO>>"%LOG_FILE%"
)

echo [CHECK][%NAME%] DST=%DST%
echo [CHECK][%NAME%] DST=%DST%>>"%LOG_FILE%"
if exist "%DST%" (
    echo [CHECK][%NAME%] target exists before: YES
    echo [CHECK][%NAME%] target exists before: YES>>"%LOG_FILE%"
) else (
    echo [CHECK][%NAME%] target exists before: NO, try mkdir
    echo [CHECK][%NAME%] target exists before: NO, try mkdir>>"%LOG_FILE%"
    mkdir "%DST%" 2>nul
    if exist "%DST%" (
        echo [CHECK][%NAME%] target exists after mkdir: YES
        echo [CHECK][%NAME%] target exists after mkdir: YES>>"%LOG_FILE%"
    ) else (
        echo [CHECK][%NAME%] target exists after mkdir: NO
        echo [CHECK][%NAME%] target exists after mkdir: NO>>"%LOG_FILE%"
    )
)

echo [CMD][%NAME%] robocopy "%SRC%" "%DST%" /MIR /FFT /R:0 /W:0 /NP %ROBOCOPY_MT_OPT% /XD "__pycache__" "build" "dist" "*.egg-info" ".git" ".svn" ".hg" ".idea" ".vscode" ".mypy_cache" /XF "*.pyc" "Thumbs.db" "desktop.ini" /LOG+:"%LOG_FILE%"

robocopy "%SRC%" "%DST%" /MIR /FFT /R:0 /W:0 /NP %ROBOCOPY_MT_OPT% ^
  /XD "__pycache__" "build" "dist" "*.egg-info" ".git" ".svn" ".hg" ".idea" ".vscode" ".mypy_cache" ^
  /XF "*.pyc" "Thumbs.db" "desktop.ini" ^
  /LOG+:"%LOG_FILE%"
echo [CHECK][%NAME%] robocopy exit code: %ERRORLEVEL%
echo [CHECK][%NAME%] robocopy exit code: %ERRORLEVEL%>>"%LOG_FILE%"
exit /b