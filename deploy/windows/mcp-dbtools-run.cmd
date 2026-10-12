@echo off
REM ============================================================
REM  mcp-dbtools 服务启动器（由计划任务或手动调用）
REM
REM  设计要点：
REM   1) 先 cd 到本脚本所在目录 —— .env / config / logs / drivers 全部是相对路径
REM   2) 若包内带便携 JRE（runtime\jre），设为 JAVA_HOME 并指向其 java.exe —— Java 桥需要 JVM
REM   3) 用 pythonw.exe 启动 —— 无控制台窗口；应用日志写 logs\app.log
REM ============================================================
setlocal
cd /d "%~dp0"

if exist "%~dp0runtime\jre\bin\server\jvm.dll" (
    set "JAVA_HOME=%~dp0runtime\jre"
    set "MCP_DBTOOLS_JAVA=%~dp0runtime\jre\bin\java.exe"
)

if not exist "logs" mkdir "logs"
if not exist "exports" mkdir "exports"

set "RUNNER=%~dp0.venv\Scripts\pythonw.exe"
if not exist "%RUNNER%" set "RUNNER=%~dp0.venv\Scripts\python.exe"
if not exist "%RUNNER%" (
    echo [ERROR] 未找到虚拟环境: %~dp0.venv\Scripts\python.exe
    echo         请先运行 install_offline.bat 完成安装
    exit /b 1
)

"%RUNNER%" -m mcp_dbtools --transport streamable-http %*
exit /b %errorlevel%
