@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not "%~1"=="" (
  echo Usage: update_translations.bat
  echo.
  echo No options. Missing glyphs in the updated translations are added to the
  echo font automatically; there is nothing to select by hand.
  exit /b 2
)
python -B tools\translations\update_translations.py
if errorlevel 1 (
  echo [update_translations] FAILED. Previous production indexes, fonts and release ZIP were kept.
  exit /b 1
)
echo [update_translations] PASS. Report: build\translations\UPDATE_REPORT.md
