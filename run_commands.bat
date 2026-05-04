@echo off
setlocal enabledelayedexpansion

cd /d G:\projects\CN_project\frontend

echo ===== COMMAND 1: npm run lint =====
call npm run lint
set LINT_EXIT=%ERRORLEVEL%
echo EXIT_CODE: !LINT_EXIT!

echo.
echo ===== COMMAND 2: npm run build =====
call npm run build
set BUILD_EXIT=%ERRORLEVEL%
echo EXIT_CODE: !BUILD_EXIT!

endlocal
