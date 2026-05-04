@echo off
REM Step 1: Start backend detached
cd /d G:\projects\CN_project
set DS_API_PORT=8888
start "ds-backend" cmd /c ".venv\Scripts\python -m backend.main"
echo Backend start command executed

REM Step 2: Start frontend detached
cd /d G:\projects\CN_project\frontend
start "ds-frontend" cmd /c "npm run dev -- --host"
echo Frontend start command executed

REM Step 3: Wait 10 seconds
echo Waiting 10 seconds for services to start...
timeout /t 10 /nobreak

REM Step 4 & 5: Verify services
cd /d G:\projects\CN_project

REM Check backend
set BACKEND_URL=none
for /f %%A in ('powershell -Command "try { $r = Invoke-WebRequest -Uri 'http://localhost:8888/status' -ErrorAction Stop -TimeoutSec 5; Write-Host 'http://localhost:8888' } catch { Write-Host 'none' }"') do set BACKEND_URL=%%A

REM Check frontend ports
set FRONTEND_URL=none
for /L %%P in (5173,1,5180) do (
    if "%FRONTEND_URL%"=="none" (
        for /f %%A in ('powershell -Command "try { $r = Invoke-WebRequest -Uri 'http://localhost:%%P' -ErrorAction Stop -TimeoutSec 3; Write-Host 'http://localhost:%%P' } catch { Write-Host 'none' }"') do (
            if "%%A" neq "none" (
                set FRONTEND_URL=%%A
            )
        )
    )
)

REM Determine overall status
set RUNNING_STATUS=not running
if not "%BACKEND_URL%"=="none" (
    if not "%FRONTEND_URL%"=="none" (
        set RUNNING_STATUS=running
    ) else (
        set RUNNING_STATUS=partial
    )
) else (
    if not "%FRONTEND_URL%"=="none" (
        set RUNNING_STATUS=partial
    )
)

REM Output results
echo.
echo BACKEND_URL=%BACKEND_URL%
echo FRONTEND_URL=%FRONTEND_URL%
echo RUNNING_STATUS=%RUNNING_STATUS%
