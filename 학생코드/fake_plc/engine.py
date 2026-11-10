"""가짜 PLC 한 대: 파일 열기, RUN/STOP, 스캔, 입력. 근거: docs/handoff/fake-plc.md 3.6, 3.7.

- .ld.txt는 sim.Plc로, 그림(.xgwx, .bin)은 해석기 DiagramPlc로 돈다. 스캔 규칙은 둘 다 sim.py다.
  .ld.txt의 그림은 layout()으로 만들어 화면에 보인다.
- D 메모리(words)는 엔진이 가진다. RUN마다 새 Plc를 만들어 P·M·T를 지우고, D는 그대로 넘긴다.
  파일을 열면 D도 지운다. 채점 때 앞 파일의 D01000·D01001이 남아 묵은 상태가 보이지 않게 한다.
- 스캔 입력은 배선 입력(wired) 위에 강제 입력(forced)을 덮은 것이다. 강제가 우선한다.
"""

import asyncio
import time
from pathlib import Path

from tools.xgwx import ladder_src, sim
from tools.xgwx.layout import encoded, layout

from fake_plc.diagram import DiagramPlc, fidelity, read_programs, unsupported

# 화면 경고 줄 (3.6의 7, 3.2, 3.7)
LATCH = "래치 설정 미확인: RUN에서 P·M·T를 지우고 D는 유지한다 (실물 설정을 확인하지 못함)"
GUESS = "추정 요소: 상승 펄스 접점(0x08)·SET(0x10)·RST(0x11)의 뜻은 표본으로 추정했다 (XG5000 화면 대조 못 함)"
ORDER = "여러 프로그램 실행 순서 미확인: XML <Program> 순서대로 한 스캔에 모두 실행한다"
CATCH_UP = 10          # 한 번 깨어날 때 최대 스캔 수 (picknplace_full.model.VirtualPlant와 같다)


class Engine:
    def __init__(self):
        self.words = {}        # D 메모리. RUN에는 남고 파일을 열면 지운다. Modbus 서버가 같은 dict를 쓴다
        self.wired = {}        # 배선 입력: 로봇이 보낸 P00004·P00005
        self.forced = {}       # 강제 입력
        self.path = None
        self.opened = 0        # 파일을 열 때마다 1씩 는다 (화면이 그림을 다시 받는다)
        self.names = []        # 파일 안 프로그램 이름 전부
        self.programs = []     # 실행할 [(이름, RungRec 목록)…]. 화면 그림도 이것이다
        self.problems = []     # 실행을 막는 줄 (열기 오류, 미지원)
        self.warnings = []     # 막지 않는 경고 줄
        self.plc = None
        self.running = False
        self.scans = 0         # RUN 뒤 스캔 수
        self._make = None      # 새 Plc를 만드는 함수

    def open(self, path, names=None) -> None:
        """파일을 연다. 돌고 있었으면 멈춘다. names는 실행할 프로그램 이름(기본은 모두, XML 순서).
        열 수 없으면 예외 대신 problems에 한 줄을 남긴다."""
        path = Path(path)
        self.stop()
        self.words.clear()     # 새 dict로 바꾸지 않는다. Modbus 서버가 이 dict를 들고 있다
        self.path, self.plc, self._make = path, None, None
        self.opened += 1
        self.names, self.programs, self.problems, self.warnings = [], [], [], [LATCH]
        try:
            if path.name.lower().endswith(".ld.txt"):
                source = ladder_src.load(path).rungs
                programs = [(path.name, encoded(layout(source)))]   # XG5000에 저장되는 모양
                self._make = lambda: sim.Plc(source)
            else:
                programs = read_programs(path)
        except Exception as err:      # 학생 파일이다. 무엇이 틀렸든 화면에 한 줄로 낸다
            self.problems = ["열 수 없음: %s" % err]
            return
        self.names = [name for name, _ in programs]
        self.programs = [(name, r) for name, r in programs if names is None or name in names]
        many = len(self.programs) > 1                 # 여럿이면 줄 앞에 프로그램 이름
        if any(e.kind in (0x08, 0x10, 0x11) for _, rungs in self.programs for r in rungs for e in r.elements):
            self.warnings.append(GUESS)
        if many:
            self.warnings.append(ORDER)
        for name, rungs in self.programs:
            prefix = name + ": " if many else ""
            self.problems += [prefix + line for line in unsupported(rungs)]
            self.warnings += [prefix + line for line in fidelity(rungs)]
        if self._make is None:
            chosen = [rungs for _, rungs in self.programs]
            self._make = lambda: DiagramPlc(chosen)

    def run(self) -> None:
        """RUN: 첫 스캔부터 다시 시작한다. P·M·T는 지우고 D는 유지한다 (3.6의 6)."""
        if self.problems or self._make is None:
            raise ValueError("\n".join(self.problems) or "파일을 먼저 연다")
        self.plc = self._make()
        self.plc.words = self.words
        self.running, self.scans = True, 0

    def stop(self) -> None:
        self.running = False

    def release(self, dev) -> None:
        """강제를 푼다. 스캔은 들어온 입력만 쓰므로 배선 값(없으면 0)을 한 번 써 둔다.
        그러지 않으면 강제했던 값이 다음 RUN까지 남는다."""
        self.forced.pop(dev, None)
        if self.plc:
            self.plc.bits[dev] = self.wired.get(dev, 0)

    def scan(self, dt_ms: int = 10) -> None:
        """한 스캔. STOP이면 아무것도 하지 않는다."""
        if self.running:
            self.plc.scan({**self.wired, **self.forced}, dt_ms)
            self.scans += 1


async def scan_forever(scan, period=0.01, clock=time.monotonic, sleep=asyncio.sleep):
    """scan()을 period마다 부른다. 고정 간격 따라잡기: n번째 스캔의 마감 = 시작 + n·period.
    Windows의 sleep(0.01)은 약 16 ms 걸리므로, 깨어날 때 마감이 지난 스캔을 몰아서 돌려 평균을 맞춘다.
    한 번에 CATCH_UP개까지만 돌린다. 그보다 많이 밀렸으면(오래 멈춤) 밀린 스캔은 버린다.
    clock과 sleep은 시험이 가짜 시계를 넣을 때만 바꾼다."""
    start, n = clock(), 0
    while True:
        due = int((clock() - start) / period) + 1 - n     # 마감이 지난 스캔 수
        for _ in range(min(due, CATCH_UP)):
            scan()
            n += 1
        if due > CATCH_UP:
            start = clock() - n * period                  # 밀린 것은 버리고 지금부터 다시 센다
        await sleep(max(0.0, start + n * period - clock()))
