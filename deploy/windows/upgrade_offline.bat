@echo off
chcp 65001 >nul
REM ============================================================
REM  mcp-dbtools Windows 离线升级脚本（处理已安装的旧版本）
REM
REM  用法（在解压的新版离线包目录下执行）:
REM    upgrade_offline.bat [旧安装目录]
REM      - 指定旧安装目录: upgrade_offline.bat D:\mcp-dbtools
REM      - 或把新版离线包解压覆盖到旧安装目录后直接运行 upgrade_offline.bat
REM
REM  功能:
REM    - 备份用户配置（.env、config\datasources.json）到 backup-时间戳\
REM    - 更新代码与依赖（dist、offline_packages_win、drivers 增量、scripts）
REM    - 保留用户文件：.env、config\datasources.json、自定义 JDBC 驱动 jar
REM    - 离线升级虚拟环境依赖（--no-index --upgrade）
REM ============================================================
setlocal
set "PKG_DIR=%~dp0"

REM 解析旧安装目录
set "INSTALL_DIR=%~1"
if "%INSTALL_DIR%"=="" set "INSTALL_DIR=%PKG_DIR%"
for %%I in ("%INSTALL_DIR%") do set "INSTALL_DIR=%%~fI"

echo 升级目标目录: %INSTALL_DIR%

REM ---------- 前置检查 ----------
if not exist "%INSTALL_DIR%\.venv\Scripts\python.exe" (
    echo 错误：%INSTALL_DIR% 不是有效的 mcp-dbtools 安装目录（未找到 .venv\Scripts\python.exe）
    echo 请确认旧版本已通过 install_offline.bat 安装，或传入旧安装目录参数
    pause & exit /b 1
)
if not exist "%PKG_DIR%dist\*.whl" (
    echo 错误：当前目录（新版离线包）缺少 dist 项目 wheel
    pause & exit /b 1
)

REM ---------- 1. 备份用户配置 ----------
for /f "tokens=1-3 delims=: " %%a in ('time /t') do set "TT=%%a%%b%%c"
set "TT=%TT: =0%"
for /f "tokens=2 delims==" %%a in ('wmic os get localdatetime /value ^| find "="') do set "DT=%%a"
set "BACKUP=%INSTALL_DIR%\backup-%DT:~0,14%"
echo 备份用户配置 -^> %BACKUP%
mkdir "%BACKUP%\config" 2>nul
if exist "%INSTALL_DIR%\.env" copy /y "%INSTALL_DIR%\.env" "%BACKUP%\" >nul
if exist "%INSTALL_DIR%\config\datasources.json" copy /y "%INSTALL_DIR%\config\datasources.json" "%BACKUP%\config\" >nul

REM ---------- 2. 更新代码与依赖（保留用户文件） ----------
echo 更新代码与依赖...
if exist "%INSTALL_DIR%\dist" rmdir /s /q "%INSTALL_DIR%\dist"
xcopy /e /i /y "%PKG_DIR%dist" "%INSTALL_DIR%\dist" >nul
if exist "%INSTALL_DIR%\offline_packages_win" rmdir /s /q "%INSTALL_DIR%\offline_packages_win"
xcopy /e /i /y "%PKG_DIR%offline_packages_win" "%INSTALL_DIR%\offline_packages_win" >nul
REM JDBC 驱动增量合并：不覆盖已有 jar
if not exist "%INSTALL_DIR%\drivers" mkdir "%INSTALL_DIR%\drivers"
if exist "%PKG_DIR%drivers\*.jar" copy /y "%PKG_DIR%drivers\*.jar" "%INSTALL_DIR%\drivers\" >nul
REM 脚本与配置示例
if not exist "%INSTALL_DIR%\scripts\sql" mkdir "%INSTALL_DIR%\scripts\sql"
if exist "%PKG_DIR%scripts\sql\*" xcopy /e /i /y "%PKG_DIR%scripts\sql" "%INSTALL_DIR%\scripts\sql" >nul
if exist "%PKG_DIR%scripts\encrypt_password.py" copy /y "%PKG_DIR%scripts\encrypt_password.py" "%INSTALL_DIR%\scripts\" >nul
if not exist "%INSTALL_DIR%\config" mkdir "%INSTALL_DIR%\config"
copy /y "%PKG_DIR%config\datasources.json.example" "%INSTALL_DIR%\config\datasources.json.example" >nul
copy /y "%PKG_DIR%requirements.txt" "%INSTALL_DIR%\requirements.txt" >nul
copy /y "%PKG_DIR%.env.example" "%INSTALL_DIR%\.env.example" >nul

REM ---------- 3. 离线升级虚拟环境依赖 ----------
echo 离线升级依赖（--no-index）...
for %%f in ("%INSTALL_DIR%\dist\*.whl") do set "PROJ_WHEEL=%%f"
"%INSTALL_DIR%\.venv\Scripts\pip" install --no-index --find-links="%INSTALL_DIR%\offline_packages_win" --upgrade "%PROJ_WHEEL%"
if errorlevel 1 (
    echo 依赖升级失败，请检查 offline_packages_win 目录完整性
    pause & exit /b 1
)

REM ---------- 4. 重启服务 ----------
echo 重启服务...
pm2 describe mcp-dbtools >nul 2>&1
if %errorlevel%==0 (
    pm2 restart mcp-dbtools --update-env
    echo   已通过 pm2 重启
) else (
    echo   [提示] 未检测到 pm2 进程，请手动重启：
    echo     %INSTALL_DIR%\.venv\Scripts\python -m mcp_dbtools --transport streamable-http
)

echo.
echo ============================================================
echo  升级完成
echo  备份目录: %BACKUP% （确认无误后可删除）
echo  健康检查: http://127.0.0.1:8000/health
echo ============================================================
endlocal
pause
