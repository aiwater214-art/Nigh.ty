@echo off
setlocal
set PROJECT_ROOT=%~dp0..
if "%DASHBOARD_PORT%"=="" set DASHBOARD_PORT=8000
if "%GAME_SERVER_PORT%"=="" set GAME_SERVER_PORT=8100
set UVICORN_OPTS=%UVICORN_OPTS%

echo Nigh.ty workflow helper
set /p CHOICE="1) Dashboard + server  2) Server only  3) Dashboard only  > "
if "%CHOICE%"=="1" goto BOTH
if "%CHOICE%"=="2" goto SERVER
if "%CHOICE%"=="3" goto DASH

echo Unknown option
exit /b 1

:BOTH
echo Launching dashboard on %DASHBOARD_PORT% and gameplay server on %GAME_SERVER_PORT%
start "Nigh.ty dashboard" cmd /k "cd /d %PROJECT_ROOT% && uvicorn app.main:app --host 0.0.0.0 --port %DASHBOARD_PORT% %UVICORN_OPTS%"
start "Nigh.ty server" cmd /k "cd /d %PROJECT_ROOT% && uvicorn server.network:create_app --factory --host 0.0.0.0 --port %GAME_SERVER_PORT% %UVICORN_OPTS%"
exit /b 0

:SERVER
echo Launching gameplay server on %GAME_SERVER_PORT%
cd /d %PROJECT_ROOT%
uvicorn server.network:create_app --factory --host 0.0.0.0 --port %GAME_SERVER_PORT% %UVICORN_OPTS%
exit /b %ERRORLEVEL%

:DASH
echo Launching dashboard on %DASHBOARD_PORT%
cd /d %PROJECT_ROOT%
uvicorn app.main:app --host 0.0.0.0 --port %DASHBOARD_PORT% %UVICORN_OPTS%
exit /b %ERRORLEVEL%
