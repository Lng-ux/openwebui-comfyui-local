@echo off
setlocal enabledelayedexpansion
title ComfyUI + Open WebUI launcher

rem ======================= ¸ÄÕâÀï =======================
set "COMFY_DIR=C:\ComfyUI"
set "OWUI_DIR=E:\owui"
set "OWUI_DATA=E:\owui-data\data"
set "COMFY_PORT=8188"
set "OWUI_PORT=8080"
rem =====================================================

echo ============================================
echo   ComfyUI  = %COMFY_DIR%  (:%COMFY_PORT%)
echo   Open UI  = %OWUI_DIR%  (:%OWUI_PORT%)
echo ============================================

netstat -ano | findstr ":%COMFY_PORT% " | findstr LISTENING >nul
if not errorlevel 1 (
  echo [1/3] ComfyUI already running
  goto owui_check
)
echo [1/3] starting ComfyUI ... this takes 40-90s (torch import + model load)
powershell -NoProfile -WindowStyle Hidden -Command "Start-Process -FilePath '%COMFY_DIR%\python_embeded\python.exe' -ArgumentList '-s','ComfyUI\main.py','--windows-standalone-build','--listen','0.0.0.0','--enable-cors-header','*' -WorkingDirectory '%COMFY_DIR%' -WindowStyle Minimized"

set /a n=0
:loop_comfy
netstat -ano | findstr ":%COMFY_PORT% " | findstr LISTENING >nul
if not errorlevel 1 goto comfy_ready
set /a n+=1
if !n! geq 180 goto comfy_fail
ping -n 2 127.0.0.1 >nul
goto loop_comfy
:comfy_ready
echo        ComfyUI ready (:%COMFY_PORT%)

:owui_check
netstat -ano | findstr ":%OWUI_PORT% " | findstr LISTENING >nul
if not errorlevel 1 (
  echo [2/3] Open WebUI already running
  goto owui_ready
)
echo [2/3] starting Open WebUI ...
set "DATA_DIR=%OWUI_DATA%"
set "OFFLINE_MODE=true"
set "HF_ENDPOINT=https://hf-mirror.com"
powershell -NoProfile -WindowStyle Hidden -Command "Start-Process -FilePath '%OWUI_DIR%\Scripts\open-webui.exe' -ArgumentList 'serve','--host','0.0.0.0','--port','%OWUI_PORT%' -WorkingDirectory '%OWUI_DIR%' -WindowStyle Minimized"

set /a n=0
:loop_owui
curl -s -o nul -w "%%{http_code}" --max-time 2 http://127.0.0.1:%OWUI_PORT%/ > "%TEMP%\owui_code.txt" 2>nul
set "code="
set /p code=<"%TEMP%\owui_code.txt"
if "!code!"=="200" goto owui_ready
set /a n+=1
if !n! geq 180 goto owui_fail
ping -n 2 127.0.0.1 >nul
goto loop_owui

:owui_ready
echo        Open WebUI ready (:%OWUI_PORT%)
echo.
echo [3/3] desktop : http://127.0.0.1:%OWUI_PORT%
echo        phone   : (same WiFi, any of these)
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do (
  for /f "tokens=* delims= " %%b in ("%%a") do (
    echo %%b | findstr /r /c:"^ *192\.168\." /c:"^ *10\." /c:"^ *172\." >nul && echo                   http://%%b:%OWUI_PORT%
  )
)
echo.
if /i "%~1"=="nobrowser" goto done
start "" http://127.0.0.1:%OWUI_PORT%
goto done

:comfy_fail
echo [ERROR] ComfyUI not up in 180s. Run %COMFY_DIR%\run_nvidia_gpu.bat manually and read its console.
goto end
:owui_fail
echo [ERROR] Open WebUI not up in 180s. Run %OWUI_DIR%\start_owui.bat manually and read its console.
goto end

:done
echo.
echo All ready. You can close this window; the services keep running.
ping -n 9 127.0.0.1 >nul
:end
endlocal
