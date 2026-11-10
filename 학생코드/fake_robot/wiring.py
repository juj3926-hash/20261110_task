"""배선 JSON 클라이언트 (docs/handoff/virtual-wiring.md 2.3·2.4).

가짜 PLC(서버, 127.0.0.1:5021)에 붙어 로봇 DI를 받고 로봇 DO를 보낸다.
- 메시지 한 개 = UTF-8 JSON 한 줄.
- 받기: {"type":"outputs","P00044":0,…} → DI0~DI3. 모르는 키·type, 깨진 줄, 0/1이 아닌 값은 무시한다.
- 보내기: {"type":"inputs","P00004":DO0,"P00005":DO1}. 붙자마자, 바뀌면 바로, 그리고 0.5초마다.
- 연결이 없으면 DI를 모두 0으로 본다. 안 붙거나 끊기면 1초마다 다시 접속한다.
"""

import asyncio
import json

DI_DEVICES = {"P00044": "DI0", "P00045": "DI1", "P00046": "DI2", "P00047": "DI3"}
DO_DEVICES = {"DO0": "P00004", "DO1": "P00005"}
RETRY_S = 1.0
RESEND_S = 0.5


class WiringClient:
    def __init__(self, host="127.0.0.1", port=5021):
        self.host, self.port = host, port
        self.di = dict.fromkeys(DI_DEVICES.values(), 0)
        self.do = dict.fromkeys(DO_DEVICES, 0)
        self.connected = False
        self._writer = None

    def set_do(self, do: dict) -> None:
        """do = {"DO0": 0/1, "DO1": 0/1}. 바뀌었으면 바로 보낸다."""
        new = {name: int(do[name]) for name in DO_DEVICES}
        if new != self.do:
            self.do = new
            self._send()

    async def run(self) -> None:
        """멈출 때(취소)까지 접속 → 주고받기 → 끊기면 1초 뒤 다시."""
        while True:
            try:
                reader, self._writer = await asyncio.open_connection(self.host, self.port)
            except OSError:
                await asyncio.sleep(RETRY_S)
                continue
            self.connected = True
            resend = asyncio.create_task(self._resend())
            try:
                self._send()
                while line := await reader.readline():
                    self._receive(line)
            except (OSError, ValueError):  # ValueError: 끝없이 긴 줄
                pass
            finally:
                resend.cancel()
                self._writer.close()
                self._writer, self.connected = None, False
                self.di = dict.fromkeys(self.di, 0)
            await asyncio.sleep(RETRY_S)

    async def _resend(self):
        while True:
            await asyncio.sleep(RESEND_S)
            self._send()

    def _send(self):
        if self._writer:
            msg = {"type": "inputs", **{DO_DEVICES[name]: v for name, v in self.do.items()}}
            self._writer.write(json.dumps(msg).encode() + b"\n")

    def _receive(self, line: bytes):
        try:
            msg = json.loads(line)
        except ValueError:  # 깨진 줄 (UTF-8이 아닌 줄 포함)
            return
        if not isinstance(msg, dict) or msg.get("type") != "outputs":
            return
        for device, name in DI_DEVICES.items():
            if msg.get(device) in (0, 1):
                self.di[name] = int(msg[device])
