@echo off
chcp 65001 >nul
cd /d "%~dp0"

set PY=C:\Users\yuu\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe
set GIT=C:\Program Files\Git\cmd\git.exe

echo ================================================
echo  Monster Girl TD - publish translation update
echo ================================================
echo.
echo [1/3] rebuild [awaked]/[notawaked] branch entries ...
"%PY%" "%~dp0tools\生成解析后形态词条.py"
if errorlevel 1 goto :err

echo.
echo [2/3] sync translations tree + rebuild manifest ...
"%PY%" "%~dp0tools\同步译文到仓库.py"
if errorlevel 1 goto :err

echo.
echo [3/3] git add + commit + push ...
"%GIT%" add -A
"%GIT%" commit -m "update translations"
"%GIT%" push

echo.
echo ================================================
echo  Done. Repo: https://github.com/yu6096516-arch/momusu-zh
echo  In game: press F6 to reload, or just restart.
echo ================================================
pause
exit /b 0

:err
echo.
echo ================================================
echo  FAILED - check the message above.
echo ================================================
pause
exit /b 1
