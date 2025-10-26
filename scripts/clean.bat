@echo off
setlocal enabledelayedexpansion
set PROJECT_ROOT=%~dp0..
if "%SNAPSHOT_DIR%"=="" set SNAPSHOT_DIR=data\snapshots
if "%WORLD_DIR%"=="" set WORLD_DIR=data\worlds
set DB_FILES=app.db test.db

echo Cleanup helper
set /p CHOICE="1) Snapshots  2) Worlds dir  3) Databases  4) __pycache__  5) All  > "
if "%CHOICE%"=="1" goto SNAP
if "%CHOICE%"=="2" goto WORLDS
if "%CHOICE%"=="3" goto DB
if "%CHOICE%"=="4" goto PYC
if "%CHOICE%"=="5" goto ALL

echo Unknown option
exit /b 1

:SNAP
call :SNAP_RUN
goto END

:WORLDS
call :WORLDS_RUN
goto END

:DB
call :DB_RUN
goto END

:PYC
call :PYC_RUN
goto END

:ALL
call :SNAP_RUN
call :WORLDS_RUN
call :DB_RUN
call :PYC_RUN
goto END

:SNAP_RUN
if exist "%PROJECT_ROOT%\%SNAPSHOT_DIR%" (
  del /q "%PROJECT_ROOT%\%SNAPSHOT_DIR%\*.json" 2>nul
  echo Cleared %SNAPSHOT_DIR%
) else (
  echo Snapshot dir not found
)
goto :EOF

:WORLDS_RUN
if exist "%PROJECT_ROOT%\%WORLD_DIR%" (
  rmdir /s /q "%PROJECT_ROOT%\%WORLD_DIR%"
)
mkdir "%PROJECT_ROOT%\%WORLD_DIR%" >nul 2>&1
echo Reset %WORLD_DIR%
goto :EOF

:DB_RUN
for %%F in (%DB_FILES%) do (
  if exist "%PROJECT_ROOT%\%%F" (
    del /q "%PROJECT_ROOT%\%%F"
    echo Deleted %%F
  )
)
goto :EOF

:PYC_RUN
for /d /r "%PROJECT_ROOT%" %%D in (__pycache__) do (
  rmdir /s /q "%%D"
)
echo Purged __pycache__ folders
goto :EOF

:END
exit /b 0
