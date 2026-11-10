"""가짜 로봇 핵심: 내장 스크립트1·2와 I/O 계약 (docs/handoff/fake-robot-3d.md 3.1·3.2).

참조 스크립트(robot/PICKNPLACE_FULL_main.js, spec 8.1)를 흉내 낸다. 시간은 step의 dt로만 흐른다.

스크립트 진행 (phase)
- 쉼: DI0이면 PICK(스크립트1), 아니고 DI1이면 HOME(스크립트2)으로 시작한다. 요청은 켜져 있는지로 본다.
- 시작: start_delay 뒤 DO0을 켜고 동작으로 간다. 그사이 요청이 꺼져도 그대로 간다.
- 동작: 지금 관절에서 목표까지 move_time 동안 선형 보간한다. 목표에 닿고 DI3가 꺼져 있으면 끝대기로 간다.
  DI3가 켜져 있으면 목표에 닿아도 동작에 머문다(moveJointSafely).
- 끝대기: DO0을 켠 채 DI0·DI1이 모두 꺼지기를 기다린다(waitAllOff). 꺼지면 DO0을 끄고 쉼으로 간다.
  이동이 0초여도 PLC가 DO0을 보게 한다(2026-11-10 강사 결정).

DI3 감시 (감시 스레드와 같다. phase와 따로 돈다)
- 꺼짐: DI3가 켜지면 감속으로 간다.
- 감속: stop_time 동안 속도를 0까지 줄인 뒤 DO1을 켜고 정지로 간다. 그사이 DI3가 꺼져도 끝까지 한다.
  쉼·끝대기에서는 움직이는 것이 없으므로 바로 DO1을 켠다(스크립트의 moveStop이 바로 돌아온다).
  시작(start_delay)에서는 stop_time을 그대로 쓴다. 그래야 스크립트처럼 DO0이 DO1보다 먼저 켜진다.
- 정지: 이동이 멈춘다. DO0은 그대로다. DI3가 꺼지면 DO1을 끄고 꺼짐으로 간다(남은 이동을 이어 감).
DI2는 보지 않는다.
"""

HOME = [0.0, 0.0, 90.0, 0.0, 90.0, 0.0]    # 기본값. 화면에서 바꾼다
PICK = [45.0, 30.0, 60.0, 0.0, 90.0, 45.0]


class Robot:
    def __init__(self, start_delay=0.2, move_time=2.0, stop_time=0.3):
        self.start_delay, self.move_time, self.stop_time = start_delay, move_time, stop_time
        self.home, self.pick = list(HOME), list(PICK)
        self.joints = list(HOME)
        self.do = {"DO0": 0, "DO1": 0}
        self.events = []          # DO 변화 순서 [("DO0", 1), ...]
        self.phase = "쉼"
        self.watch = "꺼짐"
        self.left = 0.0           # 시작: 남은 지연 (초)
        self.stop_left = 0.0      # 감속: 남은 시간 (초)
        self.progress = 0.0       # 동작: 0~1
        self.origin = self.target = None

    @property
    def state(self) -> str:
        """화면 상태: 쉼 / 동작 중 / 정지 중."""
        if self.watch != "꺼짐":
            return "정지 중"
        return "쉼" if self.phase == "쉼" else "동작 중"      # 끝대기도 DO0이 켜져 있으므로 동작 중

    def step(self, di: dict, dt: float) -> dict:
        """di = {"DI0": 0/1, …}. dt초 진행하고 DO를 돌려준다."""
        run = self._watch(di.get("DI3"), dt)      # 이번 step에 이동이 나아간 시간 (초)
        request = di.get("DI0") or di.get("DI1")
        if self.phase == "쉼" and request:
            self.target = list(self.pick if di.get("DI0") else self.home)
            self.phase, self.left = "시작", self.start_delay
        if self.phase == "시작":
            self.left = round(self.left - dt, 9)  # 0.01씩 빼도 오차가 쌓이지 않게
            if self.left <= 0:
                self.phase, self.origin, self.progress = "동작", list(self.joints), 0.0
                self._set("DO0", 1)
        elif self.phase == "동작":
            gain = run / self.move_time if self.move_time > 0 else 1.0
            k = self.progress = min(1.0, round(self.progress + gain, 9))
            self.joints = [a + (b - a) * k for a, b in zip(self.origin, self.target)]
            if k >= 1.0:
                self.joints = list(self.target)       # 반올림 오차 없이 목표 그대로
                if not di.get("DI3"):                 # 스크립트처럼 목표에 닿고 DI3가 꺼져 있어야 끝난다
                    self.phase = "끝대기"
        if self.phase == "끝대기" and not request:    # 이동이 끝난 그 step에도 본다
            self.phase = "쉼"
            self._set("DO0", 0)
        return dict(self.do)

    def _watch(self, stop, dt) -> float:
        if self.watch == "꺼짐" and stop:
            idle = self.phase in ("쉼", "끝대기")         # 움직이는 것이 없으면 감속할 것도 없다
            self.watch, self.stop_left = "감속", 0.0 if idle else self.stop_time
        if self.watch == "꺼짐":
            return dt
        if self.watch == "정지":
            if stop:
                return 0.0
            self.watch = "꺼짐"
            self._set("DO1", 0)
            return dt
        # 감속: 속도 비율 = 남은 시간 / stop_time. 나아간 시간은 그 적분이다
        r0 = self.stop_left
        r1 = self.stop_left = max(round(r0 - dt, 9), 0.0)
        if r1 == 0:
            self.watch = "정지"
            self._set("DO1", 1)
        return (r0 * r0 - r1 * r1) / (2 * self.stop_time) if self.stop_time > 0 else 0.0

    def _set(self, name, value):
        if self.do[name] != value:
            self.do[name] = value
            self.events.append((name, value))
