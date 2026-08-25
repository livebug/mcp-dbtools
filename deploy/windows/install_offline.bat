@echo off
chcp 65001 >nul
REM ============================================================
REM  mcp-dbtools Windows 离线安装脚本
REM  前置要求：
REM    1. Python 3.12（64 位，与离线包对应）
REM    2. JDK 11+（JPype 需要 JVM，推荐 17/21）
REM  用法：双击运行，或命令行执行 install_offline.bat
REM ============================================================
cd /d "%~dp0"

echo [1/3] 检查 Python...
python --version >nul 2>&1 || (
    echo 错误：未检测到 Python，请先安装 Python 3.12 并加入 PATH
    pause & exit /b 1
)

echo [2/3] 创建虚拟环境并离线安装依赖...
if not exist .venv (
    python -m venv .venv
)
.venv\Scripts\pip install --no-index --find-links=offline_packages_win -e .
if errorlevel 1 (
    echo 依赖安装失败，请检查 offline_packages_win 目录完整性
    pause & exit /b 1
)

echo [3/3] 完成！
echo.
echo ============================================================
echo  下一步：
echo   1. 配置 config\datasources.json（含 DB2 示例）
echo   2. 把 JDBC 驱动 jar 放入 drivers\ 目录
echo      （DB2: db2jcc4.jar / openGauss: opengauss-jdbc.jar）
echo   3. 启动服务：
echo      .venv\Scripts\python -m mcp_dbtools --transport streamable-http
echo      服务地址 http://127.0.0.1:8000/mcp
echo ============================================================
pause
