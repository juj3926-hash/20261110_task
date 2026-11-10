"""PICKNPLACE_FULL 가상 장비 (spec 5절). 자동 시험과 비상용 시연에 쓴다."""

import asyncio
import time
from pathlib import Path

from tools.xgwx.ladder_src import load
from tools.xgwx.sim import Plc

LADDER = Path(__file__).resolve().parents[1] / "ladder" / "PICKNPLACE_FULL.ld.txt"


class VirtualRobot:
    """로봇 스크립트(spec 8절)의 I/O 계약을 흉내 낸다. 시간은 step의 dt_s로만 흐른다.

    상태
    - 쉼: DI0 또는 DI1이 켜져 있으면 시작으로 간다.
    - 시작: start_delay 뒤 DO0을 켜고 동작으로 간다. 그사이 요청이 꺼져도 그대로 간다.
    - 동작: move_time만큼 움직인 뒤 끝대기로 간다.
    - 끝대기: DO0을 켠 채 DI0·DI1이 모두 꺼지기를 기다린다(waitAllOff). 꺼지면 DO0을 끄고 쉼으로 간다.
      이동이 0초여도 PLC가 DO0을 보게 한다(2026-11-10 강사 결정).

    즉시 정지 (감시 스레드, 상태와 상관없음)
    - DI3가 켜진 지 stop_time이 지나면 DO1을 켜고 이동 시간을 멈춘다. 그 전에는 감속 중이라 이동 시간이 흐른다.
    - DI3가 꺼지면 DO1을 끄고 남은 이동을 이어 간다. DO0은 그대로다. DI2는 보지 않는다.

    ignore_requests=True는 로봇이 자동 실행 상태가 아닌 경우다. DI0·DI1만 무시하고 DI3 처리는 같다.

    줄인 점 (가짜 로봇 fake_robot/core.py와 참조 스크립트는 이렇지 않다)
    - 쉬는 중의 DI3도 stop_time 뒤에 DO1을 켠다. 스크립트는 바로 켠다.
    - stop_time보다 짧은 DI3는 무시한다. DO1이 켜지지 않고 이동도 멈추지 않는다. 스크립트는 감속해 멈췄다가 다시 간다.
    - 즉시 정지 중(ESTOP)에는 DO1과 상관없이 비정상 정지라 상태 코드는 같다. 다만 감속 중에 재개하면 스크립트는
      멈춘 뒤 DO1을 잠깐 켜서 0003이 한 번 더 보일 수 있다. 이 흉내에는 그것이 없다.
    """

    def __init__(self, start_delay=0.2, move_time=2.0, stop_time=0.3, ignore_requests=False):
        self.start_delay, self.move_time, self.stop_time = start_delay, move_time, stop_time
        self.ignore_requests = ignore_requests
        self.state = "쉼"
        self.left = 0.0       # 시작: 남은 지연, 동작: 남은 이동 시간 (초)
        self.stop_for = 0.0   # DI3가 켜져 있은 시간 (초)

    def step(self, di, dt_s):
        req = (di.get("DI0") or di.get("DI1")) and not self.ignore_requests
        # 0.01씩 더하고 빼도 오차가 쌓이지 않게 반올림한다
        self.stop_for = round(self.stop_for + dt_s, 9) if di.get("DI3") else 0.0
        stopped = bool(di.get("DI3")) and self.stop_for >= self.stop_time
        if self.state == "쉼" and req:
            self.state, self.left = "시작", self.start_delay
        if self.state == "시작":
            self.left = round(self.left - dt_s, 9)
            if self.left <= 0:
                self.state, self.left = "동작", self.move_time
        elif self.state == "동작" and not stopped:
            self.left = round(self.left - dt_s, 9)
            if self.left <= 0:
                self.state = "끝대기"
        if self.state == "끝대기" and not req:
            self.state = "쉼"
        return {"DO0": int(self.state in ("동작", "끝대기")), "DO1": int(stopped)}


class VirtualPlant:
    """래더 원본을 스캔하는 가상 PLC와 가상 로봇. 스캔마다 시뮬레이터 시간은 scan_ms * speed다.

    스캔은 고정 간격 따라잡기 방식이다. 윈도우의 sleep이 길어도 평균 속도가 맞는다.
    """

    CATCH_UP = 10  # 한 번 깨어날 때 최대 스캔 수

    def __init__(self, ladder=LADDER, speed=1.0, robot=None, scan_ms=10):
        source = load(ladder)
        self.sym = source.symbols
        self.plc = Plc(source.rungs)
        self.robot = robot or VirtualRobot()
        self.speed, self.scan_ms = speed, scan_ms
        self._task = None

    def _scan(self):
        di = {s: self.plc.bit(self.sym[s]) for s in ("DI0", "DI1", "DI2", "DI3")}
        do = self.robot.step(di, self.scan_ms * self.speed / 1000)
        self.plc.scan({self.sym[s]: v for s, v in do.items()}, self.scan_ms * self.speed)

    async def _run(self):
        period = self.scan_ms / 1000
        start, n = time.monotonic(), 0
        while True:
            due = int((time.monotonic() - start) / period) + 1 - n
            for _ in range(min(max(due, 1), self.CATCH_UP)):
                self._scan()
                n += 1
            if due > self.CATCH_UP:  # 오래 멈췄다. 밀린 스캔은 버린다
                start, n = time.monotonic() - n * period, n
            await asyncio.sleep(max(0, start + n * period - time.monotonic()))

    async def start(self):
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self):
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    def read(self):
        return self.plc.word("D01000"), self.plc.word("D01001")

    def write(self, address, value):
        """스캔 사이(이벤트 루프 한 턴)에 적용되므로 다음 스캔 전에 반영된다."""
        self.plc.write_word(("D01000", "D01001")[address], value)

    def bit(self, symbol):
        return self.plc.bit(self.sym[symbol])


class ModelTransport:
    """Transport 형식(작업 5)을 따르는 프로세스 안 장비."""

    name = "model"

    def __init__(self, plant):
        self.plant = plant

    async def connect(self):
        await self.plant.start()
        return True

    async def read(self):
        return self.plant.read()

    async def write_command(self, code):
        self.plant.write(1, code)

    async def close(self):
        await self.plant.stop()

    def lost(self):
        return False
