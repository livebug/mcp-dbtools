"""open-dbclient Java 桥的 NDJSON-RPC 客户端。

两种连接模式：

- **spawn（默认）**：``java -jar bridge.jar`` 拉起一个桥子进程，走 stdin/stdout；
- **remote**：连接一个已在别处运行的共享桥服务（``--listen <port>``），走 TCP socket，
  多客户端共用一个 JVM。

两种模式用同一套 newline-delimited JSON 帧：请求 ``{"id","method","params"}``、响应
``{"id","ok","result"|"error"}``、事件 ``{"type":"event","method","params"}``。

设计要点（与 TypeScript 侧的 ``RpcChannel`` 对齐）：

- **传输无关**：帧与请求/响应相关性在本模块内完成，连接生命周期也在此收敛；
- **线程安全**：写帧加锁，响应按 ``id`` 派发到各自的等待线程；
- **stderr 独立排空**（仅 spawn 模式）：JDBC 驱动会往 stderr 打横幅/日志，不排空会阻塞 JVM。
"""

from __future__ import annotations

import json
import logging
import socket
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable

logger = logging.getLogger(__name__)

# 请求默认超时（秒）。查询可能很慢，调用方可按需覆盖。
DEFAULT_REQUEST_TIMEOUT = 60.0


class BridgeError(Exception):
    """桥返回的结构化错误（对应 Java 侧 RpcException）。"""

    def __init__(
        self,
        code: str,
        message: str,
        sql_state: str | None = None,
        error_code: int | None = None,
    ) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.sql_state = sql_state
        self.error_code = error_code


class BridgeNotRunningError(Exception):
    """桥未连接或已断开时发起的请求。"""


class BridgeClient:
    """管理与一个 Java 桥的连接（子进程或远程服务）。"""

    def __init__(
        self,
        jar_path: str | Path,
        java: str | None = None,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
        command: list[str] | None = None,
        remote: tuple[str, int] | None = None,
        token: str | None = None,
        tenant_id: str | None = None,
    ) -> None:
        self.jar_path = Path(jar_path)
        self.java = java or "java"
        self.request_timeout = request_timeout
        # 可覆盖启动命令（测试注入假桥进程、或包装 JVM 启动器）
        self._command = command
        # remote 模式：(host, port)；spawn 模式为 None
        self._remote = remote
        # 连接共享桥服务时所需的共享密钥（--listen --token）
        self._token = token
        # 租户标识：共享桥上按此隔离连接归属（谁开的连接谁能用）
        self._tenant_id = tenant_id

        self._proc: subprocess.Popen[Any] | None = None
        self._socket: socket.socket | None = None
        self._read_stream: Iterable[str] | None = None
        self._write_lock = threading.Lock()
        self._pending: dict[str, _Pending] = {}
        self._listeners: list[Callable[[str, dict[str, Any]], None]] = []
        self._stopping = threading.Event()
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start(self) -> None:
        """建立连接：remote 模式连 TCP，spawn 模式拉起子进程。"""
        if self.alive:
            return
        if self._remote is not None:
            self._start_remote()
        else:
            self._start_subprocess()
        self._stopping.clear()
        self._reader = threading.Thread(target=self._read_loop, name="bridge-reader", daemon=True)
        self._reader.start()

    def _start_subprocess(self) -> None:
        if self._command is not None:
            cmd = list(self._command)
        else:
            if not self.jar_path.is_file():
                raise FileNotFoundError(f"桥 jar 不存在: {self.jar_path}")
            cmd = [self.java, "-jar", str(self.jar_path)]
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except FileNotFoundError as exc:
            raise BridgeNotRunningError(
                f"找不到 Java 运行时 '{self.java}'，请安装 JDK/JRE 17+ 或设置 MCP_DBTOOLS_JAVA"
            ) from exc
        self._read_stream = self._proc.stdout
        self._stderr_reader = threading.Thread(target=self._drain_stderr, name="bridge-stderr", daemon=True)
        self._stderr_reader.start()
        logger.info("启动 Java 桥 (pid=%s): %s", self._proc.pid, self.jar_path.name)

    def _start_remote(self) -> None:
        host, port = self._remote
        try:
            self._socket = socket.create_connection((host, port), timeout=10.0)
            self._socket.settimeout(None)  # 读循环自己管理阻塞
        except OSError as exc:
            self._socket = None
            raise BridgeNotRunningError(f"无法连接桥服务 {host}:{port}: {exc}") from exc
        self._read_stream = self._socket.makefile("r", encoding="utf-8")
        logger.info("连接远程桥服务 %s:%s", host, port)

    def close(self) -> None:
        """断开连接。

        - spawn 模式：先 system.shutdown 优雅关闭【自己的】桥子进程，超时则强杀；
        - remote 模式：只关自己的 socket，【不】关闭共享桥服务。
        """
        if self._remote is not None:
            self._stopping.set()
            if self._socket is not None:
                try:
                    self._socket.close()
                except OSError:
                    pass
                self._socket = None
            return
        if self._proc is None or self._proc.poll() is not None:
            self._proc = None
            return
        self._stopping.set()
        try:
            self.request("system.shutdown", timeout=5.0)
        except Exception:  # noqa: BLE001 关闭路径尽力而为
            pass
        try:
            self._proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            logger.warning("桥未在 5s 内退出，强制终止")
            self._proc.kill()
            self._proc.wait(timeout=2.0)
        self._proc = None

    @property
    def alive(self) -> bool:
        if self._remote is not None:
            return self._socket is not None
        return self._proc is not None and self._proc.poll() is None

    # ------------------------------------------------------------------
    # 请求 / 响应
    # ------------------------------------------------------------------

    def request(
        self, method: str, params: dict[str, Any] | None = None, timeout: float | None = None
    ) -> dict[str, Any]:
        """发送一个请求，阻塞等待响应并返回 result（dict）。"""
        if not self.alive:
            raise BridgeNotRunningError("桥未连接")

        request_id = uuid.uuid4().hex
        frame_obj: dict[str, Any] = {"id": request_id, "method": method, "params": params or {}}
        if self._token:
            frame_obj["token"] = self._token
        if self._tenant_id:
            frame_obj["tenantId"] = self._tenant_id
        frame = json.dumps(frame_obj, ensure_ascii=False, separators=(",", ":"))
        pending = _Pending()
        self._pending[request_id] = pending

        try:
            with self._write_lock:
                self._write_line(frame)
            pending.event.wait(timeout if timeout is not None else self.request_timeout)
        finally:
            self._pending.pop(request_id, None)

        if pending.error is not None:
            raise pending.error
        if not pending.done:
            raise TimeoutError(f"请求 '{method}' 超时（{timeout or self.request_timeout}s）")
        return pending.result

    def on_event(self, listener: Callable[[str, dict[str, Any]], None]) -> None:
        """注册事件回调：``listener(method, params)``。"""
        self._listeners.append(listener)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _write_line(self, line: str) -> None:
        if self._socket is not None:
            self._socket.sendall((line + "\n").encode("utf-8"))
            return
        assert self._proc is not None and self._proc.stdin is not None
        self._proc.stdin.write(line + "\n")
        self._proc.stdin.flush()

    def _read_loop(self) -> None:
        stream = self._read_stream
        assert stream is not None
        try:
            for line in stream:
                line = line.strip()
                if not line:
                    continue
                try:
                    frame = json.loads(line)
                except json.JSONDecodeError as exc:
                    logger.warning("忽略桥返回的无法解析的帧: %s", exc)
                    continue
                self._dispatch(frame)
        except (OSError, ValueError) as exc:
            if not self._stopping.is_set():
                logger.error("桥连接读取失败: %s", exc)
        finally:
            if self._remote is not None:
                self._socket = None
            # 连接断开：唤醒所有等待中的请求
            for pending in self._pending.values():
                pending.fail(BridgeNotRunningError("桥连接已断开"))
            self._pending.clear()

    def _drain_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        try:
            for line in self._proc.stderr:
                msg = line.rstrip("\n")
                if msg:
                    logger.debug("[bridge] %s", msg)
        except (OSError, ValueError):
            pass

    def _dispatch(self, frame: dict[str, Any]) -> None:
        if "id" in frame and ("ok" in frame or "error" in frame):
            request_id = str(frame["id"])
            pending = self._pending.get(request_id)
            if pending is None:
                logger.warning("收到未知请求 id 的响应: %s", request_id)
                return
            if frame.get("ok"):
                pending.complete(frame.get("result") or {})
            else:
                error = frame.get("error") or {}
                pending.fail(
                    BridgeError(
                        str(error.get("code", "INTERNAL_ERROR")),
                        str(error.get("message", "unknown error")),
                        error.get("sqlState"),
                        error.get("errorCode"),
                    )
                )
            return
        if frame.get("type") == "event":
            method = str(frame.get("method", ""))
            params = frame.get("params") or {}
            for listener in list(self._listeners):
                try:
                    listener(method, params)
                except Exception:  # noqa: BLE001 回调不应打断读循环
                    logger.exception("桥事件回调失败")
            return
        logger.debug("忽略未知帧: %s", frame)


class _Pending:
    """一次请求的挂起状态。"""

    def __init__(self) -> None:
        self.event = threading.Event()
        self.result: dict[str, Any] = {}
        self.error: Exception | None = None
        self.done = False

    def complete(self, result: dict[str, Any]) -> None:
        self.result = result
        self.done = True
        self.event.set()

    def fail(self, error: Exception) -> None:
        self.error = error
        self.done = False
        self.event.set()
