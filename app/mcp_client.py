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


class MCPClient:
    """Lightweight JSON-over-stdio client for an MCP server process."""

    def __init__(self, name: str, command: str) -> None:
        self.name = name
        self.command = command
        self.process: Optional[Process] = None
        self._reader_task: Optional[asyncio.Task[None]] = None
        self._lock = asyncio.Lock()
        self._pending: Dict[str, asyncio.Future[Any]] = {}

    async def start(self) -> None:
        """Launch the MCP server process if it is not already running."""
        if self.process and self.process.returncode is None:
            return

        logger.info("starting_mcp_server", name=self.name, command=self.command)
        self.process = await asyncio.create_subprocess_shell(
            self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._reader_task = asyncio.create_task(self._read_stdout())
        asyncio.create_task(self._log_stderr())

    async def stop(self) -> None:
        """Terminate the process gracefully."""
        if not self.process:
            return
        logger.info("stopping_mcp_server", name=self.name)
        self.process.terminate()
        await self.process.wait()
        if self._reader_task:
            self._reader_task.cancel()
        self.process = None

    async def _read_stdout(self) -> None:
        assert self.process and self.process.stdout
        while True:
            line = await self.process.stdout.readline()
            if not line:
                break
            try:
                payload = json.loads(line.decode("utf-8").strip())
            except json.JSONDecodeError as exc:
                logger.error("invalid_mcp_payload", name=self.name, error=str(exc), line=line.decode())
                continue

            req_id = payload.get("id")
            if not req_id:
                logger.warning("missing_request_id", name=self.name, payload=payload)
                continue

            future = self._pending.pop(req_id, None)
            if not future:
                logger.warning("no_pending_future", name=self.name, request_id=req_id)
                continue

            if "error" in payload:
                future.set_exception(RuntimeError(payload["error"]))
            else:
                future.set_result(payload.get("result"))

    async def _log_stderr(self) -> None:
        if not self.process or not self.process.stderr:
            return
        while True:
            line = await self.process.stderr.readline()
            if not line:
                break
            logger.warning("mcp_stderr", name=self.name, message=line.decode().strip())

    async def call_tool(self, method: str, params: Dict[str, Any]) -> Any:
        """Invoke a tool on the MCP server and return its JSON result."""
        await self.start()
        if not self.process or not self.process.stdin:
            raise RuntimeError(f"MCP process {self.name} is not running")

        request_id = str(uuid.uuid4())
        payload = json.dumps({"id": request_id, "method": method, "params": params})
        future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()

        async with self._lock:
            self._pending[request_id] = future
            self.process.stdin.write(payload.encode("utf-8") + b"\n")
            await self.process.stdin.drain()

        return await future


class MCPManager:
    """Shared registry for Base and Dexscreener MCP clients."""

    def __init__(self, base_cmd: str, dexscreener_cmd: str) -> None:
        self.base = MCPClient("base", base_cmd)
        self.dexscreener = MCPClient("dexscreener", dexscreener_cmd)

    async def start(self) -> None:
        await asyncio.gather(self.base.start(), self.dexscreener.start())

    async def shutdown(self) -> None:
        await asyncio.gather(self.base.stop(), self.dexscreener.stop())
