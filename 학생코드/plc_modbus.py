"""pymodbus 3.16.1 호출과 결과 변환.

두 판이 같은 방식으로 요청을 만들고(주소 0, 수량 1, device_id=국번),
같은 문구로 실패를 기록하도록 여기에 모은다. 비동기판은 async_* 를,
동기판은 sync_* 를 쓴다. 차이는 await 여부뿐이다.
"""

from __future__ import annotations

import logging
import time

from pymodbus import FramerType
from pymodbus.client import AsyncModbusSerialClient, ModbusSerialClient
from pymodbus.exceptions import ConnectionException, ModbusIOException

from plc_core import ADDRESS, CommResult, Settings


def quiet_pymodbus() -> None:
    """pymodbus 내부 경고·오류가 stderr로 나와 화면을 깨뜨리지 않게 한다.
    실패는 예외로 받아 우리 로그에 기록한다."""
    logging.getLogger("pymodbus").setLevel(logging.CRITICAL)


def _client_kwargs(s: Settings) -> dict:
    return dict(
        framer=FramerType.RTU,
        baudrate=s.baudrate,
        bytesize=s.bytesize,
        parity=s.parity,
        stopbits=s.stopbits,
        timeout=s.timeout,
        retries=s.retries,
    )


def make_async_client(s: Settings) -> AsyncModbusSerialClient:
    """실행 중인 이벤트 루프 안에서 불러야 한다.
    pymodbus의 숨은 자동 재연결(0.1초부터 최대 300초까지 늘어남)은 끄고, 요청 직전에 연결을
    확인해 다시 연다(_ensure_link). 동기 클라이언트가 요청마다 connect()를 확인하는 것과 맞춘다."""
    return AsyncModbusSerialClient(s.port, reconnect_delay=0, **_client_kwargs(s))


def make_sync_client(s: Settings) -> ModbusSerialClient:
    return ModbusSerialClient(s.port, **_client_kwargs(s))


def to_result(response, exc: BaseException | None, elapsed: float, at: float, want_value: bool) -> CommResult:
    if exc is not None:
        if isinstance(exc, ModbusIOException):
            error = "응답 없음"
        elif isinstance(exc, ConnectionException):
            error = "연결 오류"
        else:
            error = f"{type(exc).__name__}: {exc}"
        return CommResult(False, None, error, elapsed, at)
    if response.isError():
        return CommResult(False, None, f"예외 응답 코드 {response.exception_code}", elapsed, at)
    return CommResult(True, response.registers[0] if want_value else None, "", elapsed, at)


# ---- 동기: 함수가 반환될 때까지 호출한 쪽이 멈춘다.


def sync_read(client, unit: int) -> CommResult:
    t0, response, exc = time.monotonic(), None, None
    try:
        response = client.read_holding_registers(ADDRESS, count=1, device_id=unit)
    except Exception as e:  # KeyboardInterrupt는 잡지 않음
        exc = e
    return to_result(response, exc, time.monotonic() - t0, time.time(), want_value=True)


def sync_write(client, unit: int, value: int) -> CommResult:
    t0, response, exc = time.monotonic(), None, None
    try:
        response = client.write_register(ADDRESS, value, device_id=unit)
    except Exception as e:
        exc = e
    return to_result(response, exc, time.monotonic() - t0, time.time(), want_value=False)


# ---- 비동기: 응답을 기다리는 동안 이벤트 루프가 다른 작업(키 입력, 대기 안내)을 돌린다.


def _poll_failure(client) -> BaseException | None:
    """pymodbus는 Windows에서 시리얼 포트를 폴링 작업(ctx.transport.poll_task)으로 읽는다.
    USB 어댑터가 빠지면 이 작업이 예외로 조용히 끝나고 transport는 남아서, 이후 요청은 보내지지
    않은 채 '응답 없음'이 된다. 그 상태면 끝난 작업의 예외를 돌려준다."""
    transport = getattr(getattr(client, "ctx", None), "transport", None)
    task = getattr(transport, "poll_task", None)
    if task is not None and task.done() and not task.cancelled():
        return task.exception()
    return None


async def _ensure_link(client) -> bool:
    """요청 직전 연결 확인. 끊겼거나 폴링 작업이 죽었으면 닫고 다시 연다."""
    if client.connected and _poll_failure(client) is None:
        return True
    try:
        client.close()
    except Exception:  # 빠진 장치의 핸들 닫기 실패는 무시하고 다시 연결 시도
        pass
    return await client.connect()


async def _async_call(client, call, want_value: bool) -> CommResult:
    t0, response, exc = time.monotonic(), None, None
    try:
        if not await _ensure_link(client):
            raise ConnectionException("serial port reconnect failed")
        response = await call()
    except Exception as e:  # CancelledError·KeyboardInterrupt는 잡지 않음
        exc = e
        if _poll_failure(client) is not None:  # 요청 도중 어댑터가 빠짐 → 응답 없음이 아니라 연결 오류
            exc = ConnectionException(f"serial poll stopped: {_poll_failure(client)}")
    return to_result(response, exc, time.monotonic() - t0, time.time(), want_value)


async def async_read(client, unit: int) -> CommResult:
    return await _async_call(client, lambda: client.read_holding_registers(ADDRESS, count=1, device_id=unit), True)


async def async_write(client, unit: int, value: int) -> CommResult:
    return await _async_call(client, lambda: client.write_register(ADDRESS, value, device_id=unit), False)
