@echo off
chcp 65001 >nul
cd /d "%~dp0"

where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "学习通自动打字.py"
) else (
    echo 没有找到 pythonw，改用 python 前台运行...
    python "学习通自动打字.py"
    pause
)
