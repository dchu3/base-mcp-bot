"""MCP client management for Base and Dexscreener servers."""

from __future__ import annotations

import asyncio
import json
import shlex
import uuid
from asyncio.subprocess import Process
from typing import Any, Dict, Optional

from app.utils.logging import get_logger

logger = get_logger(__name__)

JSONRPC_VERSION = "2.0"
DEFAULT_PROTOCOL_VERSION = "2024-10-07"
CLIENT_INFO = {
    "name": "base-mcp-bot",
    "version": "0.1.0",
}


class MCPClient:
    """Lightweight JSON-over-stdio client for an MCP server process."""

    def __init__(self, name: str, command: str) -> None:
        self.name = name
        self.command = command
        try:
            self._command_args = shlex.split(command)
        except ValueError as exc:  # pragma: no cover - invalid configuration is fatal
            raise ValueError(f"Invalid MCP command for {name!r}: {command}") from exc
        if not self._command_args:  # pragma: no cover - configuration failure
            raise ValueError(f"Empty MCP command for {name!r}")
        self._command_repr = " ".join(self._command_args)
        self.process: Optional[Process] = None
        self._reader_task: Optional[asyncio.Task[None]] = None
        self._stderr_task: Optional[asyncio.Task[None]] = None
        self._init_lock = asyncio.Lock()
        self._lock = asyncio.Lock()
        self._pending: Dict[str, asyncio.Future[Any]] = {}
        self._initialized = False
        self._tools: list[Dict[str, Any]] = []

    @property
    def tools(self) -> list[Dict[str, Any]]:
        """Return the list of tools available on this server."""
        return self._tools

    async def start(self) -> None:
        """Launch the MCP server process if it is not already running."""
        if self.process and self.process.returncode is None:
            await self._ensure_initialized()
            return

        logger.info("starting_mcp_server", name=self.name, command=self._command_repr)
        self.process = await asyncio.create_subprocess_exec(
            *self._command_args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if self.process and self.process.returncode is not None:
            code = self.process.returncode
            await self.stop()
            raise RuntimeError(
                f"MCP server {self.name} exited immediately with code {code}"
            )
        self._tune_stream_limits()
        self._reader_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._log_stderr())
        await self._ensure_initialized()

    def _tune_stream_limits(self) -> None:
        """Increase asyncio stream limits so large MCP payloads do not fail."""
        process = self.process
        if not process:
            return

        target_limit = 1_048_576  # 1 MiB per line is plenty for MCP JSON payloads.

        try:
            stdout = getattr(process, "stdout", None)
            if stdout is not None and hasattr(stdout, "_limit"):
                current = getattr(stdout, "_limit", 0) or 0
                if current < target_limit:
                    setattr(stdout, "_limit", target_limit)

            stderr = getattr(process, "stderr", None)
            if stderr is not None and hasattr(stderr, "_limit"):
                current = getattr(stderr, "_limit", 0) or 0
                if current < target_limit:
                    setattr(stderr, "_limit", target_limit)
        except Exception as exc:
            logger.warning("mcp_stream_limit_tune_failed", error=str(exc))

    async def stop(self) -> None:
        """Terminate the process gracefully."""
        if not self.process:
            return
        logger.info("stopping_mcp_server", name=self.name)
        self.process.terminate()
        try:
            await asyncio.wait_for(self.process.wait(), timeout=5)
        except asyncio.TimeoutError:
            logger.warning("mcp_terminate_timeout", name=self.name)
            self.process.kill()
            await self.process.wait()

        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None

        if self._stderr_task:
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except asyncio.CancelledError:
                pass
            self._stderr_task = None

        self._fail_pending(f"MCP server '{self.name}' stopped.")
        self._initialized = False
        self.process = None

    async def _read_stdout(self) -> None:
        process = self.process
        if not process or not process.stdout:
            return

        try:
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                try:
                    payload = json.loads(line.decode("utf-8").strip())
                except json.JSONDecodeError as exc:
                    logger.error(
                        "invalid_mcp_payload",
                        name=self.name,
                        error=str(exc),
                        line=line.decode(),
                    )
                    continue

                if (
                    isinstance(payload, dict)
                    and "id" in payload
                    and ("result" in payload or "error" in payload)
                ):
                    self._handle_response(payload)
                    continue

                if isinstance(payload, dict) and "method" in payload:
                    try:
                        await self._handle_request_or_notification(payload)
                    except Exception as exc:  # pragma: no cover - defensive logging
                        logger.error(
                            "mcp_message_handler_failed", name=self.name, error=str(exc)
                        )
                    continue

                logger.warning(
                    "unexpected_mcp_message", name=self.name, payload=payload
                )
        finally:
            exit_code = process.returncode if process else None
            if exit_code is None and process:
                # Allow a short chance for the subprocess to update its return code.
                exit_code = process.returncode
            if process and exit_code is not None:
                logger.info(
                    "mcp_process_exited",
                    name=self.name,
                    returncode=exit_code,
                )
            if self._pending:
                message = f"MCP server '{self.name}' stopped before replying."
                if exit_code is not None:
                    message += f" Exit code: {exit_code}."
                self._fail_pending(message.strip())
            self._initialized = False

    async def _log_stderr(self) -> None:
        if not self.process or not self.process.stderr:
            return
        while True:
            try:
                line = await self.process.stderr.readline()
            except asyncio.CancelledError:
                break
            if not line:
                break
            logger.warning("mcp_stderr", name=self.name, message=line.decode().strip())

    async def call_tool(self, method: str, params: Dict[str, Any]) -> Any:
        """Invoke a tool on the MCP server and return its JSON result."""
        await self.start()
        if not self.process or not self.process.stdin:
            raise RuntimeError(f"MCP process {self.name} is not running")

        if self.process.returncode is not None:
            code = self.process.returncode
            await self.stop()
            raise RuntimeError(f"MCP process {self.name} exited with code {code}")

        result = await self._send_request(
            "tools/call",
            {
                "name": method,
                "arguments": params or {},
            },
        )

        if isinstance(result, dict):
            if result.get("isError"):
                message = (
                    self._extract_content_text(result.get("content"))
                    or "MCP tool call failed."
                )
                raise RuntimeError(message)

            structured = result.get("structuredContent")
            if structured is not None:
                return structured

            content_text = self._extract_content_text(result.get("content"))
            if content_text is not None:
                try:
                    return json.loads(content_text)
                except (TypeError, json.JSONDecodeError):
                    return {"content": content_text}

        return result

    def _fail_pending(self, message: str) -> None:
        if not self._pending:
            return
        for request_id, future in list(self._pending.items()):
            self._pending.pop(request_id, None)
            if not future.done():
                future.set_exception(RuntimeError(message))

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return

            try:
                await asyncio.wait_for(
                    self._send_request(
                        "initialize",
                        {
                            "protocolVersion": DEFAULT_PROTOCOL_VERSION,
                            "capabilities": {"tools": {}},
                            "clientInfo": CLIENT_INFO,
                        },
                    ),
                    timeout=10,
                )
                await self._send_notification("notifications/initialized", {})
            except Exception as exc:
                await self.stop()
                raise RuntimeError(
                    f"MCP server {self.name} failed to initialize: {exc}"
                ) from exc

            try:
                tools_response = await asyncio.wait_for(
                    self._send_request("tools/list", {}), timeout=10
                )
                if isinstance(tools_response, dict):
                    self._tools = [
                        t
                        for t in tools_response.get("tools", [])
                        if isinstance(t, dict) and t.get("name")
                    ]
                    names = [t["name"] for t in self._tools]
                    if names:
                        logger.info("mcp_tools_available", name=self.name, tools=names)
            except Exception as exc:  # pragma: no cover - non-fatal warning
                logger.warning("mcp_list_tools_failed", name=self.name, error=str(exc))

            self._initialized = True

    async def _handle_request_or_notification(self, payload: Dict[str, Any]) -> None:
        method = payload.get("method")
        if method == "ping" and "id" in payload:
            await self._send_response(payload["id"], {})
            return

        if method == "notifications/tools/list_changed":
            logger.info("mcp_tools_list_changed", name=self.name)
            try:
                tools = await self._send_request("tools/list", {})
                if isinstance(tools, dict):
                    names = [
                        tool.get("name")
                        for tool in tools.get("tools", [])
                        if isinstance(tool, dict) and tool.get("name")
                    ]
                    if names:
                        logger.info("mcp_tools_refreshed", name=self.name, tools=names)
            except Exception as exc:  # pragma: no cover - best-effort refresh
                logger.warning(
                    "mcp_tools_refresh_failed", name=self.name, error=str(exc)
                )
            return

        if "id" in payload:
            await self._send_error_response(
                payload["id"],
                code=-32601,
                message=f"Unsupported request method '{method}' from MCP server.",
            )
            return

        logger.debug("mcp_notification_ignored", name=self.name, method=method)

    def _handle_response(self, payload: Dict[str, Any]) -> None:
        req_id_raw = payload.get("id")
        if req_id_raw is None:
            logger.warning("missing_request_id", name=self.name, payload=payload)
            return
        req_id = str(req_id_raw)
        future = self._pending.pop(req_id, None)
        if not future:
            logger.warning("no_pending_future", name=self.name, request_id=req_id)
            return

        if "error" in payload:
            error_obj = payload["error"] or {}
            message = ""
            if isinstance(error_obj, dict):
                message = error_obj.get("message") or ""
            if not message:
                message = str(error_obj)
            future.set_exception(RuntimeError(message))
        else:
            future.set_result(payload.get("result"))

    async def _send_request(
        self, method: str, params: Optional[Dict[str, Any]] = None
    ) -> Any:
        if not self.process or not self.process.stdin:
            raise RuntimeError(f"MCP process {self.name} is not running")

        request_id = str(uuid.uuid4())
        message: Dict[str, Any] = {
            "jsonrpc": JSONRPC_VERSION,
            "id": request_id,
            "method": method,
        }
        if params is not None:
            message["params"] = params

        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()

        async with self._lock:
            self._pending[request_id] = future
            try:
                await self._write_locked(message)
            except Exception as exc:
                self._pending.pop(request_id, None)
                if not future.done():
                    future.set_exception(
                        RuntimeError(
                            f"Failed to write to MCP process {self.name}: {exc}"
                        )
                    )
                raise RuntimeError(f"MCP process {self.name} is unavailable") from exc

        return await future

    async def _send_notification(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> None:
        async with self._lock:
            await self._write_locked(
                {
                    "jsonrpc": JSONRPC_VERSION,
                    "method": method,
                    **({"params": params} if params is not None else {}),
                }
            )

    async def _send_response(
        self,
        request_id: Any,
        result: Optional[Dict[str, Any]] = None,
    ) -> None:
        async with self._lock:
            await self._write_locked(
                {
                    "jsonrpc": JSONRPC_VERSION,
                    "id": request_id,
                    "result": result or {},
                }
            )

    async def _send_error_response(
        self, request_id: Any, code: int, message: str
    ) -> None:
        async with self._lock:
            await self._write_locked(
                {
                    "jsonrpc": JSONRPC_VERSION,
                    "id": request_id,
                    "error": {
                        "code": code,
                        "message": message,
                    },
                }
            )

    async def _write_locked(self, message: Dict[str, Any]) -> None:
        if not self.process or not self.process.stdin:
            raise RuntimeError(f"MCP process {self.name} is not running")
        data = (json.dumps(message) + "\n").encode("utf-8")
        self.process.stdin.write(data)
        await self.process.stdin.drain()

    @staticmethod
    def _extract_content_text(content: Any) -> Optional[str]:
        if not isinstance(content, list):
            return None
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    return text
        return None


class MCPManager:
    """Shared registry for configured MCP clients."""

    def __init__(
        self, base_cmd: str, dexscreener_cmd: str, honeypot_cmd: str | None = None
    ) -> None:
        self.base = MCPClient("base", base_cmd)
        self.dexscreener = MCPClient("dexscreener", dexscreener_cmd)
        self.honeypot = (
            MCPClient("honeypot", honeypot_cmd)
            if honeypot_cmd and honeypot_cmd.strip()
            else None
        )

    async def start(self) -> None:
        tasks = [self.base.start(), self.dexscreener.start()]
        if self.honeypot:
            tasks.append(self.honeypot.start())
        await asyncio.gather(*tasks)

    async def shutdown(self) -> None:
        tasks = [self.base.stop(), self.dexscreener.stop()]
        if self.honeypot:
            tasks.append(self.honeypot.stop())
        await asyncio.gather(*tasks)

    def get_available_tools(self) -> list[Dict[str, Any]]:
        """Aggregate tools from all registered MCP clients."""
        all_tools = []
        clients = [self.base, self.dexscreener]
        if self.honeypot:
            clients.append(self.honeypot)

        for client in clients:
            for tool in client.tools:
                tool_copy = tool.copy()
                # Namespace the tool name: 'client.method'
                original_name = tool_copy.get("name")
                if original_name:
                    tool_copy["name"] = f"{client.name}.{original_name}"
                    all_tools.append(tool_copy)

        return all_tools
