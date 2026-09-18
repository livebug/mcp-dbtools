@echo off
chcp 65001 >nul
REM ============================================================
REM  mcp-dbtools 服务管理（Windows）
REM
REM  用法: mcp-dbtools-service.cmd <命令>
REM    install  创建计划任务（管理员=开机自启 SYSTEM；普通用户=登录自启）
REM    start    启动服务
REM    stop     停止服务
REM    restart  重启服务
REM    status   查看任务状态与监听端口
REM    logs     跟踪应用日志（Ctrl+C 退出）
REM    remove   删除计划任务（不删除安装目录与配置）
REM
REM  说明：若目标机已装 pm2 且由 pm2 管理 mcp-dbtools，本脚本自动委托给 pm2，
REM        以兼容原部署方式；否则使用 Windows 自带计划任务（无需 Node/pm2）。
REM ============================================================
setlocal EnableDelayedExpansion
set "HERE=%~dp0"
set "TASK=mcp-dbtools"
set "RUNNER=%HERE%mcp-dbtools-run.cmd"

if "%~1"=="" goto usage

set "USE_PM2="
where pm2 >nul 2>&1 && ( pm2 describe "%TASK%" >nul 2>&1 && set "USE_PM2=1" )

if /i "%~1"=="install" goto do_install
if /i "%~1"=="start"   goto do_start
if /i "%~1"=="stop"    goto do_stop
if /i "%~1"=="restart" goto do_restart
if /i "%~1"=="status"  goto do_status
if /i "%~1"=="logs"    goto do_logs
if /i "%~1"=="remove"  goto do_remove
goto usage

:do_install
echo 安装目录: %HERE%
if not exist "%RUNNER%" (
    echo [ERROR] 未找到 %RUNNER%
    echo         请在离线包解压目录内运行本脚本
    goto fail
)
echo.
echo [1/2] 尝试创建【开机自启】任务（SYSTEM 账户，需要管理员权限）...
schtasks /create /tn "%TASK%" /tr "\"%RUNNER%\"" /sc onstart /ru SYSTEM /rl HIGHEST /f
if not errorlevel 1 (
    echo   已创建: 开机自启（SYSTEM）
    goto install_done
)
echo.
echo [2/2] 未获得管理员权限，改用【登录自启】任务（当前用户）...
schtasks /create /tn "%TASK%" /tr "\"%RUNNER%\"" /sc onlogon /f
if errorlevel 1 (
    for %%I in ("%RUNNER%") do set "RUNNER_SHORT=%%~sI"
    schtasks /create /tn "%TASK%" /tr "!RUNNER_SHORT!" /sc onlogon /f
)
if errorlevel 1 (
    echo [ERROR] 计划任务创建失败，可手动以管理员执行：
    echo   schtasks /create /tn "%TASK%" /tr "\"%RUNNER%\"" /sc onstart /ru SYSTEM /rl HIGHEST /f
    goto fail
)
echo   已创建: 登录自启（当前用户）
:install_done
echo.
echo 启动: %~nx0 start    停止: %~nx0 stop    状态: %~nx0 status
goto done

:do_start
if defined USE_PM2 (
    echo [pm2] 启动 mcp-dbtools
    pm2 start "%TASK%"
    goto done
)
schtasks /run /tn "%TASK%" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] 计划任务 %TASK% 不存在，请先执行: %~nx0 install
    goto fail
)
echo 已启动。健康检查: curl http://127.0.0.1:8000/health
goto done

:do_stop
if defined USE_PM2 (
    echo [pm2] 停止 mcp-dbtools
    pm2 stop "%TASK%"
    goto done
)
schtasks /end /tn "%TASK%" >nul 2>&1
if errorlevel 1 echo [提示] 任务未在运行或不存在
echo 已停止
goto done

:do_restart
call "%~f0" stop
ping -n 2 127.0.0.1 >nul
call "%~f0" start
goto done

:do_status
if defined USE_PM2 (
    pm2 describe "%TASK%"
    goto done
)
echo === 计划任务 ===
schtasks /query /tn "%TASK%" /fo list 2>nul | findstr /i "TaskName Status Next"
if errorlevel 1 echo [提示] 计划任务 %TASK% 不存在（请先执行 %~nx0 install）
echo.
echo === 监听端口 8000 ===
netstat -ano | findstr ":8000" | findstr LISTENING
echo.
echo 健康检查: curl http://127.0.0.1:8000/health
goto done

:do_logs
echo 日志目录: %HERE%logs
dir /b "%HERE%logs" 2>nul
echo.
echo 跟踪 logs\app.log（Ctrl+C 退出）...
powershell -NoProfile -Command "if (Test-Path '%HERE%logs\app.log') { Get-Content -Wait -Tail 50 '%HERE%logs\app.log' } else { Write-Host 'app.log 不存在：服务可能未启动，或日志仅输出到控制台' }"
goto done

:do_remove
if defined USE_PM2 (
    echo [pm2] 删除 mcp-dbtools
    pm2 delete "%TASK%"
    goto done
)
schtasks /delete /tn "%TASK%" /f
echo 已删除计划任务（安装目录、.env 与配置均未删除）
goto done

:usage
echo 用法: %~nx0 ^<install^|start^|stop^|restart^|status^|logs^|remove^>
goto fail

:done
endlocal
exit /b 0

:fail
endlocal
exit /b 1
