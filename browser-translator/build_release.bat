@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem Two editions:
rem   build_release.bat                  standard (45 files, no build runtime)
rem   build_release.bat --with-toolchain also bundles the portable build runtime
rem                                    so an unknown official version can be
rem                                    rebuilt on the player's machine.
rem
rem Before --with-toolchain, run once on a developer machine:
rem   python -B tools\autobuild\build_toolchain.py
if /I "%~1"=="--with-toolchain" (
  python tools\build_release.py --with-toolchain
  goto :done
)
if not "%~1"=="" goto :usage
python tools\build_release.py
goto :done

:done
if errorlevel 1 (
  echo 打包失败。
  pause
  exit /b 1
)
echo 打包完成。
exit /b 0

:usage
echo Usage:
echo   build_release.bat                     standard release ZIP
echo   build_release.bat --with-toolchain   release ZIP + portable build runtime
exit /b 1
