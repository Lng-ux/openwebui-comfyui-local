@echo off
cd /d "%~dp0"
.\python_embeded\python.exe -s ComfyUI\main.py --windows-standalone-build --listen 0.0.0.0 --enable-cors-header *
echo If you see this and ComfyUI did not start, update your Nvidia driver.
pause
