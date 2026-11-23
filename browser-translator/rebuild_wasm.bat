@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

rem Supported developer invocations. No version auto-discovery, no URL input.
rem   rebuild_wasm.bat                        local inputs (branch 0)
rem   rebuild_wasm.bat --update <version>     download /0/ (DMM) and rebuild
rem   rebuild_wasm.bat --update <version> 0   same as above, explicit
rem   rebuild_wasm.bat --update <version> 1   download /1/ (FANZA) and rebuild
if "%~1"=="" goto :local
if /I not "%~1"=="--update" goto :usage
if "%~2"=="" goto :usage
if "%~4"=="" goto :arg_ok
goto :usage

:arg_ok
if "%~3"=="" (
  python -B tools\wasm\rebuild_wasm.py --update "%~2" --branch 0
  goto :done
)
if "%~3"=="0" (
  python -B tools\wasm\rebuild_wasm.py --update "%~2" --branch 0
  goto :done
)
if "%~3"=="1" (
  python -B tools\wasm\rebuild_wasm.py --update "%~2" --branch 1
  goto :done
)
goto :usage

:local
python -B tools\wasm\rebuild_wasm.py --branch 0
goto :done

:done
if errorlevel 1 (
  echo [rebuild_wasm] FAILED. Inputs are switched atomically; no release resource was overwritten.
  exit /b 1
)
echo [rebuild_wasm] PASS. Output: build\wasm\BUILD_REPORT.md
exit /b 0

:usage
echo Usage:
echo   rebuild_wasm.bat                          Local offline rebuild (branch 0)
echo   rebuild_wasm.bat --update 202611021000    Download /0/ (DMM) and rebuild
echo   rebuild_wasm.bat --update 202611021000 0  Same, explicit branch 0
echo   rebuild_wasm.bat --update 202611021000 1  Download /1/ (FANZA) and rebuild
echo.
echo The version must be a numeric official build id, e.g. 202611021000.
echo The optional third argument is the resource branch: 0 (DMM, default) or 1 (FANZA).
echo No automatic version discovery, no URL input, no other options.
exit /b 1
