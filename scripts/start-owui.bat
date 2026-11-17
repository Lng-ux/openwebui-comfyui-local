@echo off
title Open WebUI
rem ======================= ¸ÄÕâÀï =======================
set "OWUI_DIR=E:\owui"
set "OWUI_DATA=E:\owui-data\data"
set "OWUI_PORT=8080"
rem =====================================================
set "DATA_DIR=%OWUI_DATA%"
set "OFFLINE_MODE=true"
set "HF_ENDPOINT=https://hf-mirror.com"
cd /d "%OWUI_DIR%"
"%OWUI_DIR%\Scripts\open-webui.exe" serve --host 0.0.0.0 --port %OWUI_PORT% >> "%OWUI_DIR%\owui.log" 2>&1
