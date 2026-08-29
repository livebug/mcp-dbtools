@echo off
chcp 65001 >nul
REM ============================================================
REM  mcp-dbtools Windows 离线安装脚本
REM  前置要求：
REM    1. Python 3.13（64 位，与离线包对应；安装时勾选 "Add to PATH"）
REM    2. JDK 11+（JPype 需要 JVM，推荐 17/21）
REM  用法：双击运行，或命令行执行 install_offline.bat
REM ============================================================
setlocal
cd /d "%~dp0"

echo [1/5] 检查 Python...
python --version >nul 2>&1 || (
    echo 错误：未检测到 Python，请先安装 Python 3.13（64 位）并加入 PATH
    pause & exit /b 1
)

echo [2/5] 检查 Java（JPype 需要 JVM）...
java -version >nul 2>&1 || (
    echo 错误：未检测到 Java（JDK 11+），请先安装并加入 PATH
    pause & exit /b 1
)

echo [3/5] 创建虚拟环境并离线安装依赖...
if not exist .venv (
    python -m venv .venv
)
REM 离线安装：项目 wheel（dist\） + Windows 依赖 wheel（offline_packages_win\）
for %%f in (dist\*.whl) do set "PROJ_WHEEL=%%f"
if not defined PROJ_WHEEL (
    echo 错误：dist 目录缺少项目 wheel，请确认离线包完整
    pause & exit /b 1
)
.venv\Scripts\pip install --no-index --find-links=offline_packages_win "%PROJ_WHEEL%"
if errorlevel 1 (
    echo 依赖安装失败，请检查 offline_packages_win 目录完整性
    pause & exit /b 1
)

echo [4/5] 初始化配置...
if not exist .env (
    copy /y .env.example .env >nul
    echo   - 已生成 .env，请修改其中的密码/端口/鉴权 Token
) else (
    echo   - .env 已存在，跳过
)
if not exist config\datasources.json (
    if exist config\datasources.json.example (
        copy /y config\datasources.json.example config\datasources.json >nul
    )
)
if not exist logs mkdir logs

echo [5/5] 完成！
echo.
echo ============================================================
echo  下一步：
echo   1. 修改 .env 中的端口/鉴权 Token
echo   2. 配置 config\datasources.json（数据源）
echo   3. 确认 JDBC 驱动 jar 已在 drivers\ 目录
echo   4. 启动服务：
echo      .venv\Scripts\python -m mcp_dbtools --transport streamable-http
echo      服务地址 http://127.0.0.1:8000/mcp
echo   5. 升级：解压新版离线包后双击 upgrade_offline.bat
echo ============================================================
endlocal
pause
