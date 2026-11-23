@echo off
chcp 65001 >nul
set PY="C:\Users\yuu\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if not exist %PY% set PY=pythonw
start "" %PY% "%~dp0活动查找工具.py"