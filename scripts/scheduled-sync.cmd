@echo off
rem What a scheduler runs: wait for the network, sync, notify. All the logic is in Python (scheduled.py).
where py >nul 2>nul && (py -3 -I "%~dp0..\src\mirror.py" scheduled %* & exit /b %errorlevel%)
python -I "%~dp0..\src\mirror.py" scheduled %*
exit /b %errorlevel%
