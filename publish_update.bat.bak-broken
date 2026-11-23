@echo off
cd /d "%~dp0"
set PY=C:\Users\yuu\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe
set GIT=C:\Program Files\Git\cmd\git.exe
set TOOL=C:\Users\yuu\Desktop\????\????.py
echo ================================================
echo  Monster Girl TD - publish translation update
echo ================================================
echo.
echo [1/3] rebuild translations tree + manifest ...
"%PY%" "%TOOL%"
echo.
echo [2/3] git add + commit ...
"%GIT%" add -A
"%GIT%" commit -m "update translations"
echo.
echo [3/3] git push ...
"%GIT%" push
echo.
echo ================================================
echo  Done. Repo: https://github.com/yu6096516-arch/momusu-zh
echo  In game: press F6 to reload, or just restart.
echo ================================================
pause
