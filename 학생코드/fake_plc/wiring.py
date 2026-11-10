"""배선 JSON 서버 (docs/handoff/virtual-wiring.md 2.3, 2.4). 가짜 PLC가 서버, 가짜 로봇이 클라이언트다.

- 메시지 한 개 = UTF-8 JSON 한 줄 + "\\n".
- PLC → 로봇: {"type":"outputs","P00044":0,"P00045":0,"P00046":0,"P00047":0}
  붙으면 바로, 값이 바뀌면 그 스캔에, 바뀌지 않아도 0.5초마다 보낸다. STOP이면 모두 0이다.
- 로봇 → PLC: {"type":"inputs","P00004":0,"P00005":0} → engine.wired (강제 입력이 우선한다).
- 모르는 키, 모르는 type, 깨진 줄은 무시하고 연결은 유지한다. 끊기면 로봇 입력은 0이다.
"""

import asyncio
import contextlib
import json

INPUTS = ("P00004", "P00005")                       # 로봇 DO0, DO1
OUTPUTS = ("P00044", "P00045", "P00046", "P00047")  # 로봇 DI0~DI3
RESEND = 0.5                                        # 바뀌지 않아도 다시 보내는 간격(초)


class Wiring:
    def __init__(self, engine):
        self.engine = engine
        self.writers = set()                        # 붙어 있는 로봇
        self.sent, self.sent_at = None, 0.0         # 마지막으로 보낸 출력과 시각
        self.server = None

    def outputs(self) -> dict:
        """로봇에 보낼 출력. STOP이면 모두 0 (실물의 출력 유지 설정은 확인하지 못함)."""
        return {d: self.engine.plc.bit(d) if self.engine.running else 0 for d in OUTPUTS}

    def _line(self, out) -> bytes:
        return (json.dumps({"type": "outputs", **out}, separators=(",", ":")) + "\n").encode()

    def publish(self, now: float) -> None:
        """스캔마다 부른다. 값이 바뀌었거나 0.5초가 지났으면 붙어 있는 로봇 모두에게 보낸다."""
        out = self.outputs()
        if out != self.sent or now - self.sent_at >= RESEND:
            for writer in self.writers:
                writer.write(self._line(out))
            self.sent, self.sent_at = out, now

    def receive(self, line: bytes) -> None:
        """로봇이 보낸 한 줄. 아는 키(P00004, P00005)의 0·1만 받는다."""
        try:
            msg = json.loads(line)
        except (ValueError, RecursionError):        # 깨진 줄, 너무 깊은 JSON
            return
        if isinstance(msg, dict) and msg.get("type") == "inputs":
            for dev in INPUTS:
                if msg.get(dev) in (0, 1):
                    self.engine.wired[dev] = int(msg[dev])

    async def _serve(self, reader, writer):
        self.writers.add(writer)
        writer.write(self._line(self.outputs()))    # 붙으면 바로
        try:
            while True:
                try:
                    line = await reader.readline()
                except ValueError:                  # 너무 긴 줄: asyncio가 버렸다. 계속 읽는다
                    continue
                if not line:
                    break                           # 끊김
                self.receive(line)
        except ConnectionError:
            pass
        finally:
            self.writers.discard(writer)
            # 끊기면 로봇 입력은 0. 스캔은 들어온 입력만 쓰므로 지우지 말고 0을 넣는다
            self.engine.wired.update(dict.fromkeys(INPUTS, 0))
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()

    async def start(self, host="127.0.0.1", port=5021) -> int:
        """서버를 연다. 돌려주는 값: 실제 포트."""
        self.server = await asyncio.start_server(self._serve, host, port)
        return self.server.sockets[0].getsockname()[1]

    async def close(self) -> None:
        for writer in list(self.writers):
            writer.close()                          # 읽기에 막힌 연결을 풀어 준다
        self.server.close()
        await self.server.wait_closed()
