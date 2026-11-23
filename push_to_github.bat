@echo off
cd /d "%~dp0"
echo ============================================
echo  Push Monster Girl TD translation to GitHub
echo  Repo: https://github.com/yu6096516-arch/momusu-zh
echo ============================================
echo.
echo A login window will pop up on first push.
echo   - Choose  "Sign in with your browser"  (easiest)
echo   - Or choose Token and paste the ghp_... string
echo.
"C:\Program Files\Git\cmd\git.exe" push -u origin main
echo.
echo ============================================
if errorlevel 1 (
  echo PUSH FAILED - screenshot the red text above
) else (
  echo PUSH OK - open https://github.com/yu6096516-arch/momusu-zh
)
echo ============================================
pause
