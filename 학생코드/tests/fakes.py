"""시험용 도우미와 가짜 장치."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import datetime
from types import SimpleNamespace

from pymodbus.exceptions import ConnectionException, ModbusIOException

from plc_core import CommResult

T1 = datetime(2026, 11, 5, 13, 1, 5, 120000).timestamp()  # 13:01:05.120


def ok(v: int, at: float = T1) -> CommResult:
    return CommResult(True, v, "", 0.03, at)


def fail() -> CommResult:
    return CommResult(False, None, "응답 없음", 2.0, T1 + 9)


def idx(lines: list[str], needle: str) -> int:
    """needle이 처음 들어 있는 줄 번호."""
    return next(i for i, line in enumerate(lines) if needle in line)


class ScriptKeys:
    """생성 시각 기준으로 정해 둔 초가 지나면 글자를 내놓는 KeySource."""

    def __init__(self, script: list[tuple[float, str]]):
        self.t0 = time.monotonic()
        self.pending = sorted(script)
        self.buf: deque[str] = deque()

    def _release(self) -> None:
        now = time.monotonic() - self.t0
        while self.pending and self.pending[0][0] <= now:
            self.buf.extend(self.pending.pop(0)[1])

    def kbhit(self) -> bool:
        self._release()
        return bool(self.buf)

    def getwch(self) -> str:
        self._release()
        return self.buf.popleft()


class FakeResponse:
    def __init__(self, registers: list[int] | None = None, error_code: int | None = None):
        self.registers = registers or []
        self.exception_code = error_code or 0
        self._error = error_code is not None

    def isError(self) -> bool:  # noqa: N802 (pymodbus 이름 그대로)
        return self._error


class _FakeDevice:
    """PLC 흉내: 워드 하나(value). fail_units 국번은 응답 없음, raise_exc가 있으면 그 예외."""

    def __init__(self, value=0, delay=0.0, fail_units=(), raise_exc=None, connect_ok=True):
        self.value = value
        self.delay = delay
        self.fail_units = tuple(fail_units)
        self.raise_exc = raise_exc
        self.connect_ok = connect_ok
        self.calls: list[tuple] = []
        self.closed = False
        self.active = 0
        self.max_active = 0

    def _respond(self, kind: str, address: int, arg: int, device_id: int) -> FakeResponse:
        self.calls.append((kind, address, arg, device_id))
        if self.raise_exc is not None:
            raise self.raise_exc
        if device_id in self.fail_units:
            raise ModbusIOException("no response")
        if kind == "write":
            self.value = arg
            return FakeResponse()
        return FakeResponse(registers=[self.value])

    def close(self) -> None:
        self.closed = True


class FakeSyncClient(_FakeDevice):
    def connect(self) -> bool:
        return self.connect_ok

    def read_holding_registers(self, address, *, count=1, device_id=1):
        time.sleep(self.delay)
        return self._respond("read", address, count, device_id)

    def write_register(self, address, value, *, device_id=1):
        time.sleep(self.delay)
        return self._respond("write", address, value, device_id)


class FakeAsyncClient(_FakeDevice):
    connected = True  # pymodbus 클라이언트의 connected 속성

    async def connect(self) -> bool:
        return self.connect_ok

    async def _call(self, kind, address, arg, device_id):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay)
            return self._respond(kind, address, arg, device_id)
        finally:
            self.active -= 1

    async def read_holding_registers(self, address, *, count=1, device_id=1):
        return await self._call("read", address, count, device_id)

    async def write_register(self, address, value, *, device_id=1):
        return await self._call("write", address, value, device_id)


class _DeadTask:
    """예외로 끝난 asyncio 작업 흉내 (pymodbus 폴링 작업)."""

    def __init__(self, exc: BaseException):
        self._exc = exc

    def done(self) -> bool:
        return True

    def cancelled(self) -> bool:
        return False

    def exception(self) -> BaseException:
        return self._exc


class UnpluggableAsyncClient(FakeAsyncClient):
    """Windows에서 USB 어댑터가 빠졌을 때의 pymodbus 비동기 클라이언트 흉내.

    pymodbus는 시리얼 포트를 폴링 작업(ctx.transport.poll_task)으로 읽는데, 어댑터가 빠지면
    이 작업이 SerialException으로 끝나고 transport는 남는다. 그 뒤 요청은 보내지지 않은 채
    timeout까지 기다리다 ModbusIOException(응답 없음)이 된다.
    """

    def __init__(self, replug_ok: bool = True, unplug_during_call: bool = False, **kw):
        super().__init__(**kw)
        self.replug_ok = replug_ok
        self.unplug_during_call = unplug_during_call
        self.ctx = SimpleNamespace(transport=SimpleNamespace(poll_task=None))
        self.connects = 0

    def unplug(self) -> None:
        self.ctx.transport.poll_task = _DeadTask(OSError("ClearCommError failed"))

    @property
    def connected(self) -> bool:
        return self.ctx.transport is not None

    def close(self) -> None:
        super().close()
        self.ctx.transport = None

    async def connect(self) -> bool:
        self.connects += 1
        if not self.replug_ok:
            return False
        self.ctx.transport = SimpleNamespace(poll_task=None)
        return True

    async def _call(self, kind, address, arg, device_id):
        if self.ctx.transport is None:
            raise ConnectionException("Not connected")
        if self.unplug_during_call:
            self.unplug_during_call = False
            self.unplug()
        if self.ctx.transport.poll_task is not None:  # 폴링 작업이 죽음 → 요청이 보내지지 않음
            await asyncio.sleep(self.delay)
            raise ModbusIOException("no response")
        return await super()._call(kind, address, arg, device_id)
