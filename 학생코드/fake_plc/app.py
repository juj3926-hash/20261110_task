"""가짜 PLC 한 대를 띄운다: 엔진 + Modbus TCP + 배선 JSON + 모니터 UI + 스캔 고리 (docs/handoff/fake-plc.md).

엔진은 이벤트 루프 안에서만 만진다. 스캔, Modbus 요청, 배선 메시지가 같은 루프에서 돌므로 읽기·쓰기는
스캔 사이에 들어간다. 모니터 UI(http.server)는 스레드에서 돌고, 요청을 루프로 넘겨 처리한다.
"""

import asyncio
import threading
import time
from collections import deque

from fake_plc.engine import Engine, scan_forever
from fake_plc.modbus import start_modbus
from fake_plc.web import make_http
from fake_plc.wiring import Wiring


class FakePlc:
    def __init__(self, path):
        self.engine = Engine()
        self.engine.open(path)
        if not self.engine.problems:
            self.engine.run()                       # 전원을 켠 PLC처럼 바로 RUN
        self.modbus_log = deque(maxlen=100)         # 모니터의 Modbus 기록
        self.wiring = Wiring(self.engine)
        self.ports = {}

    async def start(self, modbus_port=5020, wiring_port=5021, http_port=8878, host="127.0.0.1"):
        """서버 셋과 스캔 고리를 띄운다. 포트 0이면 OS가 고른 포트가 self.ports에 들어간다."""
        self.modbus, self.ports["modbus"] = await start_modbus(self.engine.words, self.modbus_log, host, modbus_port)
        self.ports["wiring"] = await self.wiring.start(host, wiring_port)
        self.http = make_http(self, asyncio.get_running_loop(), http_port)
        self.ports["http"] = self.http.server_port
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.scanning = asyncio.create_task(scan_forever(self.step))

    def step(self):
        """한 스캔: 엔진을 스캔하고, 바뀐 출력을 로봇에 보낸다."""
        self.engine.scan(10)
        self.wiring.publish(time.monotonic())

    async def close(self):
        self.scanning.cancel()
        await asyncio.gather(self.scanning, return_exceptions=True)
        await asyncio.to_thread(self.http.shutdown)
        self.http.server_close()
        await self.wiring.close()
        await self.modbus.shutdown()
