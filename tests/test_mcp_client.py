import asyncio
from types import SimpleNamespace

import pytest

from app.mcp_client import MCPClient


class FakeProcess:
    """Minimal stubbed process that reports an immediate exit."""

    def __init__(self, returncode: int = 1) -> None:
        self.returncode = returncode
        self.stdout = SimpleNamespace(_limit=0)
        self.stderr = SimpleNamespace(_limit=0)
        self.stdin = None

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        return None

    async def wait(self) -> int:
        return self.returncode


@pytest.mark.asyncio
async def test_mcp_client_raises_when_process_exits_immediately(monkeypatch) -> None:
    """start() should surface an error if the subprocess dies on boot."""

    async def fake_create_subprocess_exec(*args, **kwargs):
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    client = MCPClient("base", "echo noop")

    with pytest.raises(RuntimeError):
        await client.start()
