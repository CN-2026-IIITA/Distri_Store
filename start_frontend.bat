@echo off
REM Start Frontend Service - Vite dev server
REM This runs in the current window

cd /d "G:\projects\CN_project\frontend"

echo.
echo ====================================================
echo   DistriStore - Frontend Service (Vite)
echo ====================================================
echo.
echo Starting Vite development server...
echo.
echo Frontend will typically be at: http://localhost:5173
echo.

REM Activate venv from parent and run npm dev
call ..\\.venv\Scripts\activate
npm run dev -- --host

REM If it stops, keep window open
pause
