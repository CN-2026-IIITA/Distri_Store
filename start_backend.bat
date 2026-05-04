@echo off
REM Start Backend Service - DistriStore API on port 8888
REM This opens the backend in a new window

cd /d "G:\projects\CN_project"

REM Set API port
set DS_API_PORT=8888

echo.
echo ====================================================
echo   DistriStore - Backend Service (Port 8888)
echo ====================================================
echo.
echo Starting FastAPI backend...
echo.

REM Activate venv and run backend
call .venv\Scripts\activate
python -m backend.main

REM If the backend stops, keep window open
pause
