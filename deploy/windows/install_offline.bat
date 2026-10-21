@echo off
chcp 65001 >nul
REM ============================================================
REM  mcp-dbtools Windows 离线安装脚本
REM
REM  前置要求：
REM    - 轻量包：目标机需 Python 3.13（64 位）与 JDK/JRE 17+
REM    - 自包含包（--self-contained）：目标机**无需任何预装**，
REM      脚本自动使用包内 runtime\python（便携 CPython）与 runtime\jre（Temurin JRE）
REM    - 服务托管：有 pm2 则沿用 pm2；没有则用 Windows 自带计划任务（无需 Node）
REM
REM  用法：双击运行，或命令行执行 install_offline.bat
REM ============================================================
setlocal
cd /d "%~dp0"

echo [1/5] 检查 Python...
REM 离线包内依赖为 cp3xx win_amd64 二进制 wheel，Python 版本必须与构建时一致
set "REQ_PY=3.13"
if exist "python-version-required.txt" set /p REQ_PY=<"python-version-required.txt"
set "REQ_PY=%REQ_PY: =%"
if not defined REQ_PY set "REQ_PY=3.13"

set "PY=python"
python -c "import sys; raise SystemExit(0 if '.'.join(map(str,sys.version_info[:2]))=='%REQ_PY%' else 1)" >nul 2>&1
if errorlevel 1 (
    if exist "runtime\python\python.exe" (
        set "PY=%~dp0runtime\python\python.exe"
        echo   - 系统 Python 缺失或版本不符，改用包内便携运行时: runtime\python
    ) else (
        python --version >nul 2>&1
        if errorlevel 1 (
            echo 错误：未检测到 Python。本离线包需要 Python %REQ_PY%（64 位）
        ) else (
            for /f "tokens=2" %%v in ('python --version 2^>^&1') do set "CUR_PY=%%v"
            echo 错误：当前 Python 为 %CUR_PY%，本离线包需要 Python %REQ_PY%.x（64 位）
            echo       包内依赖 wheel 为 cp%REQ_PY:.=% win_amd64，不能跨 Python 版本使用。
        )
        call :offer_python_install
        pause & exit /b 1
    )
)
"%PY%" -c "import sys; print('  - Python', sys.version.split()[0], '->', sys.executable)"

echo [2/5] 检查 Java（Java 桥 bridge.jar 需要 JRE 17+）...
set "SYS_JAVA="
set "JMAJ="
java -version >nul 2>&1 && set "SYS_JAVA=1"
if defined SYS_JAVA (
    for /f "tokens=3" %%v in ('java -version 2^>^&1 ^| findstr /i version') do set "JV=%%~v"
    for /f "tokens=1 delims=." %%m in ("%JV%") do set "JMAJ=%%m"
)
REM Java 8 显示 1.8.x，低于 17 一律改用包内便携 JRE
if defined JMAJ if %JMAJ% LSS 17 set "SYS_JAVA="
if not defined SYS_JAVA (
    if exist "runtime\jre\bin\server\jvm.dll" (
        set "JAVA_HOME=%~dp0runtime\jre"
        set "PATH=%~dp0runtime\jre\bin;%PATH%"
        set "SYS_JAVA=1"
        echo   - 使用包内便携 JRE: runtime\jre
        echo     服务启动时由 mcp-dbtools-run.cmd 自动设置 JAVA_HOME
    )
)
if not defined SYS_JAVA (
    echo 错误：未检测到可用的 Java（需 JRE/JDK 17+，Java 桥 bridge.jar 依赖）
    echo       请安装 JRE/JDK 17+ 后重试，或改用自带运行时的自包含包：
    echo         bash scripts/build_offline.sh --platform windows --self-contained
    pause & exit /b 1
)

echo [3/5] 创建虚拟环境并离线安装依赖...
if not exist .venv (
    "%PY%" -m venv .venv
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
if not exist exports mkdir exports

echo [5/5] 注册服务（开机/登录自启）...
set "USE_PM2="
where pm2 >nul 2>&1 && ( pm2 describe mcp-dbtools >nul 2>&1 && set "USE_PM2=1" )
if defined USE_PM2 goto svc_pm2
goto svc_task

:svc_pm2
echo   - 检测到 pm2 管理着 mcp-dbtools，沿用 pm2 方式
powershell -NoProfile -Command "$p=(Get-Location).Path -replace '\\','/'; if (Test-Path 'ecosystem-windows.config.cjs') { (Get-Content -Raw 'ecosystem-windows.config.cjs') -replace 'D:/mcp-dbtools', $p | Set-Content -Encoding UTF8 'ecosystem-windows.config.cjs' }" >nul 2>&1
pm2 restart mcp-dbtools --update-env || pm2 start ecosystem-windows.config.cjs
pm2 save
goto svc_done

:svc_task
echo   - 未使用 pm2，改用 Windows 计划任务（无需 Node/pm2）
set "MK="
set /p MK=  是否现在创建自启任务？[Y/n] 
if /i "%MK%"=="n" (
    echo   - 已跳过。稍后可执行: mcp-dbtools-service.cmd install
) else (
    call mcp-dbtools-service.cmd install
)
goto svc_done

:svc_done
echo.
echo ============================================================
echo  下一步：
echo   1. 修改 .env 中的端口/鉴权 Token
echo   2. 配置 config\datasources.json（数据源）
echo   3. 确认 JDBC 驱动 jar 已在 drivers\ 目录
echo   4. 启动 / 停止 / 状态 / 日志：
echo      mcp-dbtools-service.cmd start ^| stop ^| status ^| logs
echo      或前台手动运行: mcp-dbtools-run.cmd
echo      服务地址 http://127.0.0.1:8000/mcp   健康检查 /health
echo   5. 升级：解压新版离线包后双击 upgrade_offline.bat
echo   6. 二次开发：源码在 source\ 目录（开发依赖 wheel 在 offline_packages_win\）
echo      详见 README-二次开发.md
echo ============================================================
endlocal
pause
exit /b 0

REM ============================================================
REM  子过程：提示/执行包内自带的 CPython 安装器
REM  （内网机器 Python 版本过低或缺失时，可离线并行安装，不影响系统已有 Python）
REM ============================================================
:offer_python_install
if not exist "python-*-amd64.exe" (
    echo.
    echo 提示：本包未附带 Python 安装器。请在内网自备 Python %REQ_PY%.x（64 位）安装包，
    echo       或在构建机执行 bash scripts/build_offline.sh --platform windows --with-python 重新打包。
    goto :eof
)
for %%f in ("python-*-amd64.exe") do set "PYEXE=%%~ff"
echo.
echo 本包已附带 CPython 安装器：%PYEXE%
echo 可静默并行安装（不影响系统现有 Python）：
echo   "%PYEXE%" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0
set /p RUNINSTALL=是否现在安装？[y/N] 
if /i "%RUNINSTALL%"=="y" (
    "%PYEXE%" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0
    echo 安装结束。请【重新打开命令行窗口】使 PATH 生效，然后重新运行 install_offline.bat。
)
goto :eof
