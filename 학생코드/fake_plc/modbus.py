"""Modbus TCP 서버 (docs/handoff/virtual-wiring.md 2.2). pymodbus 3.16.1의 ModbusTcpServer를 쓴다.

- 국번 1. 보유 레지스터 주소 n ↔ D(1000+n). 실물 Cnet 매핑과 같다.
- FC03(읽기), FC06(한 개 쓰기), FC16(여러 개 쓰기)만 받는다. 그 밖의 FC는 예외 01.
- 예외 0B: 국번이 1이 아님. 수량이 틀린 요청(FC03 수량 0 등)은 pymodbus가 풀지 못해 0x80 01로 답한다.
- 읽기는 16비트로 자른다(값 & 0xFFFF). 쓰기는 받은 값(0~65535)을 그대로 D에 넣는다.
- 요청 처리와 스캔이 같은 이벤트 루프에서 돌므로 읽기·쓰기는 스캔 사이에 들어간다.
- 받은 요청과 응답을 log에 한 줄씩 남긴다 (모니터 UI의 Modbus 기록).
"""

from datetime import datetime

from pymodbus.constants import ExcCodes
from pymodbus.pdu import ExceptionResponse, ModbusPDU
from pymodbus.server import ModbusTcpServer
from pymodbus.simulator import SimData, SimDevice

UNIT = 1
BASE = 1000                      # 주소 n ↔ D(1000+n)
FCS = (3, 6, 16)


class _Refuse(ModbusPDU):
    """FC03·06·16이 아닌 요청. pymodbus가 FC마다 이 클래스로 풀어 예외 01로 답한다."""

    async def datastore_update(self, context, device_id):
        return ExceptionResponse(self.function_code, ExcCodes.ILLEGAL_FUNCTION)


_REFUSE = [type("Fc%02d" % fc, (_Refuse,), {"function_code": fc}) for fc in range(1, 128) if fc not in FCS]


class DMemory:
    """pymodbus 서버의 저장소. 주소를 D 디바이스로 바꿔 words(엔진의 D 메모리)를 읽고 쓴다."""

    def __init__(self, words):
        self.words = words

    def device_ids(self):
        return [UNIT]

    async def async_getValues(self, device_id, func_code, address, count=1):
        if device_id != UNIT:
            return ExcCodes.GATEWAY_NO_RESPONSE
        return [self.words.get("D%05d" % (BASE + address + i), 0) & 0xFFFF for i in range(count)]

    async def async_setValues(self, device_id, func_code, address, values):
        if device_id != UNIT:
            return ExcCodes.GATEWAY_NO_RESPONSE
        for i, v in enumerate(values):
            self.words["D%05d" % (BASE + address + i)] = v
        return None


def _request(pdu):
    unit = "" if pdu.dev_id == UNIT else "국번 %d " % pdu.dev_id
    if pdu.function_code == 3:
        return unit + "FC03 주소 %d 수량 %d" % (pdu.address, pdu.count)
    if pdu.function_code in (6, 16):
        return unit + "FC%02d 주소 %d 값 %s" % (pdu.function_code, pdu.address, ", ".join(map(str, pdu.registers)))
    return unit + "FC%02d" % pdu.function_code


def _response(pdu):
    if isinstance(pdu, ExceptionResponse):
        return "예외 %02X" % pdu.exception_code
    if pdu.function_code == 3:
        return ", ".join(map(str, pdu.registers))
    return "정상"


def _tracer(log):
    """pymodbus의 trace_pdu. 요청을 받으면 한 줄을 더하고, 응답을 보내면 그 줄 끝에 결과를 붙인다."""
    waiting = {}                                   # (transaction, 국번) → 응답을 기다리는 줄

    def trace(sending, pdu):
        key = (pdu.transaction_id, pdu.dev_id)
        if not sending:
            waiting[key] = {"at": datetime.now().strftime("%H:%M:%S.%f")[:-3], "text": _request(pdu)}
            log.append(waiting[key])
        elif key in waiting:
            waiting.pop(key)["text"] += " → " + _response(pdu)
        return pdu
    return trace


async def start_modbus(words, log, host="127.0.0.1", port=5020):
    """Modbus TCP 서버를 연다. 돌려주는 값: (서버, 실제 포트). 끝낼 때는 await server.shutdown()."""
    server = ModbusTcpServer(SimDevice(UNIT, simdata=SimData(0)),   # 자리만 채운다. 아래에서 DMemory로 바꾼다
                             address=(host, port), trace_pdu=_tracer(log), custom_pdu=_REFUSE)
    server.context = DMemory(words)             # pymodbus는 요청마다 server.context에 읽기·쓰기를 맡긴다
    await server.serve_forever(background=True)
    return server, server.transport.sockets[0].getsockname()[1]
