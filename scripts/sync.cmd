@echo off
rem Mirror new activity and push. Usage: scripts\sync.cmd [--dry-run] [folder ...]
rem A thin wrapper: all the logic is in Python (src\contribution_mirror\sync.py).
where py >nul 2>nul && (py -3 -I "%~dp0..\src\mirror.py" sync %* & exit /b %errorlevel%)
python -I "%~dp0..\src\mirror.py" sync %*
exit /b %errorlevel%
