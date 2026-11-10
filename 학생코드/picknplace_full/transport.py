"""연결 대상: 시리얼(RTU), Modbus TCP, COM 포트 자동 찾기, 모델 (spec 6.2절)."""

from __future__ import annotations

from typing import Awaitable, Callable, Protocol

from pymodbus import FramerType
from pymodbus.client import AsyncModbusSerialClient, AsyncModbusTcpClient
from pymodbus.pdu import ExceptionResponse

from plc_modbus import _ensure_link, _poll_failure, quiet_pymodbus
from picknplace_full.status import TransportError

UNIT = 1
USB_WORDS = ("CP210", "CH340", "FTDI", "USB")
OK, NO_REPLY = "응답 있음", "응답 없음"
OPEN_FAIL = "열기 실패 (다른 프로그램이 사용 중일 수 있음 — QModMaster 확인)"


class Transport(Protocol):
    name: str

    async def connect(self) -> bool: ...
    async def read(self) -> tuple[int, int]: ...
    async def write_command(self, code: int) -> None: ...
    async def close(self) -> None: ...
    def lost(self) -> bool: ...


def _serial_client(port: str, timeout: float) -> AsyncModbusSerialClient:
    return AsyncModbusSerialClient(
        port, framer=FramerType.RTU, baudrate=9600, bytesize=8, parity="N", stopbits=1,
        timeout=timeout, retries=0, reconnect_delay=0)


class _ModbusTransport:
    """클라이언트 하나를 감싼다. 요청 전에 연결을 확인하고, 모든 실패를 TransportError로 바꾼다."""

    name = ""

    def __init__(self, timeout: float):
        self.timeout = timeout
        self.client = None

    def _new_client(self):
        raise NotImplementedError

    async def connect(self) -> bool:
        quiet_pymodbus()
        if self.client is None:
            self.client = self._new_client()
        try:
            return bool(await self.client.connect())
        except Exception:
            return False

    async def _call(self, make_request):
        if self.client is None:
            raise TransportError("연결되지 않았습니다")
        try:
            if not await _ensure_link(self.client):
                raise TransportError("%s 연결 실패" % self.name)
            response = await make_request(self.client)
        except TransportError:
            raise
        except Exception as e:
            failure = _poll_failure(self.client)
            raise TransportError("%s 통신 오류: %s" % (self.name, failure or e)) from e
        if response.isError():
            raise TransportError("%s 응답 오류: %s" % (self.name, response))
        return response

    async def read(self) -> tuple[int, int]:
        r = await self._call(lambda c: c.read_holding_registers(0, count=2, device_id=UNIT))
        return r.registers[0], r.registers[1]

    async def write_command(self, code: int) -> None:
        await self._call(lambda c: c.write_register(1, code, device_id=UNIT))

    async def close(self) -> None:
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                pass

    def lost(self) -> bool:
        return self.client is not None and _poll_failure(self.client) is not None


class SerialTransport(_ModbusTransport):
    def __init__(self, port: str, timeout: float = 2.0):
        super().__init__(timeout)
        self.name = port

    def _new_client(self):
        return _serial_client(self.name, self.timeout)


class TcpTransport(_ModbusTransport):
    def __init__(self, host: str, port: int, timeout: float = 2.0):
        super().__init__(timeout)
        self.host, self.port = host, port
        self.name = "%s:%d" % (host, port)

    def _new_client(self):
        return AsyncModbusTcpClient(self.host, port=self.port, timeout=self.timeout, retries=0, reconnect_delay=0)


async def probe_port(port: str, timeout: float = 0.5) -> str:
    """포트를 열어 보유 레지스터 0~1을 한 번 읽어 본다."""
    quiet_pymodbus()
    client = _serial_client(port, timeout)
    try:
        if not await client.connect():
            return OPEN_FAIL
        r = await client.read_holding_registers(0, count=2, device_id=UNIT)
    except Exception:
        return NO_REPLY
    finally:
        try:
            client.close()
        except Exception:
            pass
    if isinstance(r, ExceptionResponse):
        return "예외 응답 코드 %d" % r.exception_code
    return NO_REPLY if r.isError() else OK


class AutoTransport:
    """COM 포트를 하나씩 시험해 처음 응답하는 포트에 연결한다."""

    name = "auto"

    def __init__(self, list_ports=None, probe: Callable[[str], Awaitable[str]] | None = None,
                 timeout: float = 2.0, emit: Callable[[str, str], None] | None = None):
        if list_ports is None:
            from serial.tools.list_ports import comports as list_ports
        self._list_ports = list_ports
        self._probe = probe or probe_port
        self._timeout = timeout
        self.emit = emit or (lambda kind, msg: None)
        self.results: list[tuple[str, str]] = []
        self.port: str | None = None
        self._inner = None

    def _candidates(self):
        found = list(self._list_ports())
        usb = [p for p in found if any(w in (p.description or "").upper() for w in USB_WORDS)]
        return usb + [p for p in found if p not in usb]

    async def _open(self, port: str):
        inner = SerialTransport(port, self._timeout)
        if not await inner.connect():
            raise TransportError(OPEN_FAIL)
        return inner

    async def rescan(self) -> bool:
        await self.close()
        self.results = []
        for p in self._candidates():
            result = await self._probe(p.device)
            if result == OK:
                try:
                    self._inner = await self._open(p.device)
                except TransportError:
                    result = OPEN_FAIL
            self.results.append((p.device, result))
            self.emit("포트", "%s (%s): %s%s" % (p.device, p.description, result, " → 사용" if result == OK else ""))
            if result == OK:
                self.port = p.device
                return True
        return False

    async def connect(self) -> bool:
        return await self.rescan()

    async def read(self) -> tuple[int, int]:
        if self._inner is None:
            raise TransportError("연결되지 않았습니다")
        return await self._inner.read()

    async def write_command(self, code: int) -> None:
        if self._inner is None:
            raise TransportError("연결되지 않았습니다")
        await self._inner.write_command(code)

    async def close(self) -> None:
        inner, self._inner, self.port = self._inner, None, None
        if inner is not None:
            await inner.close()

    def lost(self) -> bool:
        # 사용 중인 포트가 목록에서 사라졌을 때만 True. 목록에 있으면 read()가 같은 포트로 다시 연결한다.
        return self.port is not None and self.port not in [p.device for p in self._list_ports()]


def make_transport(target: str, speed: float = 1.0) -> Transport:
    if target == "auto":
        return AutoTransport()
    if target == "model":
        from picknplace_full.model import ModelTransport, VirtualPlant
        return ModelTransport(VirtualPlant(speed=speed))
    kind, _, rest = target.partition(":")
    if kind == "serial" and rest:
        return SerialTransport(rest)
    if kind == "tcp":
        host, _, port = rest.rpartition(":")
        if host and port.isdigit():
            return TcpTransport(host, int(port))
    raise ValueError("알 수 없는 대상: %r (auto, serial:COMn, tcp:HOST:PORT, model)" % target)
