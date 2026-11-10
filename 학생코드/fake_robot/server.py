"""가짜 로봇 서버: 실시간 루프, 배선 클라이언트, 3D 화면 HTTP (docs/handoff/fake-robot-3d.md).

- 내장 모드: Robot(core.py)을 고정 간격 따라잡기 루프로 돌린다. 브라우저가 없어도 PLC와 주고받는다.
- 학생 스크립트 모드: 브라우저 Worker(static/worker.js)가 rodi.js로 스크립트를 돌린다. Worker는 POST /api/io로
  DO 변화를 순서대로 보내고 DI를 받는다. 이 동안 Robot은 DO를 내지 않는다.
- HTTP는 127.0.0.1에서만 받는다. Host·Origin이 이 서버의 127.0.0.1 주소가 아니면 403, 틀린 JSON과 큰 본문은
  400이다(월요일 app.py 방식). 모든 상태는 이벤트 루프 스레드에서만 바꾼다.
"""

import argparse
import asyncio
import json
import math
import os
import threading
import time
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from fake_robot.core import Robot
from fake_robot.wiring import WiringClient

HERE = Path(__file__).resolve().parent
DT = 0.01              # 실시간 루프 한 step (초)
CATCH_UP = 10          # 한 번 깨어날 때 최대 step 수. 더 밀리면 버린다
QUIET_S = 1.0          # 학생 스크립트 모드에서 Worker 소식이 이만큼 없으면 DO를 끈다
MAX_BODY = 16384
STATES = ("쉼", "동작 중", "정지 중")
KINDS = ("경고", "오류", "상태")
DO_NAMES = {f"DO{i}" for i in range(16)}
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8"}


async def fixed_loop(tick, dt=DT, catch_up=CATCH_UP):
    """tick(dt)를 실제 시간에 맞춰 부른다(마감 시각 기준). 한 번 깨어날 때 최대 catch_up번.
    그보다 더 밀리면 나머지는 버린다. 그래서 루프가 오래 막히면 로봇 시간이 실제보다 늦게 간다."""
    deadline = time.monotonic()
    while True:
        now, n = time.monotonic(), 0
        while deadline <= now and n < catch_up:
            tick(dt)
            deadline += dt
            n += 1
        if deadline <= now:
            deadline = now + dt
        await asyncio.sleep(deadline - time.monotonic())


class FakeRobot:
    """가짜 로봇 하나. tick은 실시간 루프가, 나머지는 HTTP 처리기가 이벤트 루프 안에서 부른다."""

    def __init__(self, wiring, robot=None):
        self.wiring = wiring
        self.robot = robot or Robot()
        self.mode = "builtin"
        self.script_do = {"DO0": 0, "DO1": 0}
        self.script_joints, self.script_state, self.script_seen = list(self.robot.joints), "쉼", 0.0
        self.log, self.n = deque(maxlen=300), 0
        self.connected, self.shown_state = False, self.robot.state

    def tick(self, dt):
        if self.wiring.connected != self.connected:
            self.connected = self.wiring.connected
            self.say("상태", "PLC 연결됨" if self.connected else "PLC 연결 없음: DI를 모두 0으로 본다")
        if self.mode == "builtin":
            do = self.robot.step(self.wiring.di, dt)
            self.log_do(self.robot.events)
            self.robot.events.clear()
            self.show_state(self.robot.state)
            self.wiring.set_do(do)
        elif any(self.script_do.values()) and time.monotonic() - self.script_seen > QUIET_S:
            self.say("상태", "Worker 소식 없음: DO를 끈다 (스크립트가 안 돌면 출력 LOW)")
            self.script_events([("DO0", 0), ("DO1", 0)])

    def io(self, events, log, joints, state):
        """Worker가 보낸 DO 변화·로그·관절·상태를 받고 지금 DI를 돌려준다. 스크립트 모드가 아니면 무시한다."""
        if self.mode == "script":
            self.script_seen = time.monotonic()
            for kind, text in log:
                self.say(kind, text)
            self.script_events(events)
            self.script_joints, self.script_state = joints, state
            self.show_state(state)
        return {"di": self.di(), "mode": self.mode}

    def script_events(self, events):
        """DO 변화를 하나씩 PLC로 보낸다. 짧은 펄스도 잃지 않는다. PLC에 이어진 것은 DO0·DO1뿐이다."""
        for name, value in events:
            if name in self.script_do:
                if self.script_do[name] == value:
                    continue
                self.script_do[name] = value
                self.wiring.set_do(self.script_do)
            self.say("DO", f"{name} → {value}")

    def set_mode(self, mode):
        if mode == self.mode:
            return
        joints = list(self.joints())
        self.mode = mode
        self.wiring.set_do({"DO0": 0, "DO1": 0})       # 모드를 바꾸면 출력은 0에서 시작한다
        if mode == "builtin":
            old = self.robot
            self.robot = Robot(old.start_delay, old.move_time, old.stop_time)
            self.robot.home, self.robot.pick, self.robot.joints = old.home, old.pick, joints
            self.say("상태", "내장 스크립트 모드")
        else:
            self.script_do = {"DO0": 0, "DO1": 0}
            self.script_joints, self.script_state, self.script_seen = joints, "쉼", time.monotonic()
            self.say("상태", "학생 스크립트 모드: 스크립트 파일을 여세요")
        self.shown_state = "쉼"

    def set_joints(self, home, pick):
        self.robot.home, self.robot.pick = home, pick
        self.say("상태", "HOME·PICK 바꿈 (다음 이동부터)")

    def di(self):
        return [self.wiring.di[f"DI{i}"] for i in range(4)]

    def joints(self):
        return self.robot.joints if self.mode == "builtin" else self.script_joints

    def snapshot(self, since=0):
        builtin = self.mode == "builtin"
        do = self.robot.do if builtin else self.script_do
        return {"mode": self.mode, "connected": self.wiring.connected, "plc": f"{self.wiring.host}:{self.wiring.port}",
                "di": self.di(), "do": [do["DO0"], do["DO1"]],
                "state": self.robot.state if builtin else self.script_state,
                "joints": self.joints(), "home": self.robot.home, "pick": self.robot.pick,
                "log": [e for e in self.log if e["n"] > since], "last": self.n}   # last가 줄면 화면은 서버가 다시 켜진 것으로 안다

    def show_state(self, state):
        if state != self.shown_state:
            self.shown_state = state
            self.say("상태", state)

    def log_do(self, events):
        for name, value in events:
            self.say("DO", f"{name} → {value}")

    def say(self, kind, text):
        self.n += 1
        self.log.append({"n": self.n, "t": datetime.now().strftime("%H:%M:%S.%f")[:-3], "kind": kind, "text": text})


def numbers6(value, name, limit=math.inf):
    if not (isinstance(value, list) and len(value) == 6 and all(
            type(x) in (int, float) and math.isfinite(x) and abs(x) <= limit for x in value)):
        raise ValueError(f"{name}은 숫자 6개 (deg)" + ("" if limit == math.inf else f", -{limit}~{limit}"))
    return [float(x) for x in value]


def check_mode(p):
    if p["mode"] not in ("builtin", "script"):
        raise ValueError("mode는 builtin 또는 script")
    return (p["mode"],)


def check_joints(p):
    return numbers6(p["home"], "home", 360), numbers6(p["pick"], "pick", 360)


def check_io(p):
    events, log = p["events"], p["log"]
    if not (isinstance(events, list) and len(events) <= 200 and all(
            isinstance(e, list) and len(e) == 2 and e[0] in DO_NAMES and type(e[1]) is int and e[1] in (0, 1)
            for e in events)):
        raise ValueError("events는 [DO 이름, 0 또는 1]의 목록")
    if not (isinstance(log, list) and len(log) <= 50 and all(
            isinstance(x, list) and len(x) == 2 and x[0] in KINDS and isinstance(x[1], str) and len(x[1]) <= 500
            for x in log)):
        raise ValueError("log는 [종류, 글]의 목록")
    if p["state"] not in STATES:
        raise ValueError("state는 쉼, 동작 중, 정지 중")
    return events, log, numbers6(p["joints"], "joints"), p["state"]


POSTS = {"/api/mode": ({"mode"}, check_mode, "set_mode"),
         "/api/joints": ({"home", "pick"}, check_joints, "set_joints"),
         "/api/io": ({"events", "log", "joints", "state"}, check_io, "io")}


class _Server(ThreadingHTTPServer):
    allow_reuse_address = os.name != "nt"  # 윈도우에서 켜면 두 번째 가짜 로봇도 같은 포트를 열어 요청을 나눠 받는다


def make_server(robot, loop, port=8879):
    """static/ 아래 파일과 vendor/의 js, 그리고 /api/*를 내주는 HTTP 서버. 시작은 부르는 쪽이 한다."""
    files = {"/" + p.name: p for p in (HERE / "static").iterdir() if p.suffix in TYPES}
    files.update({"/vendor/" + p.name: p for p in (HERE / "vendor").iterdir() if p.suffix == ".js"})
    if "/index.html" in files:
        files["/"] = files["/index.html"]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def call(self, name, *args):
            """robot.name(*args)를 이벤트 루프에서 부르고 200으로 답한다.
            루프가 닫혔거나(끝내는 중) 2초 안에 답이 없으면 503이다. 연결을 그냥 끊지 않는다."""
            async def run():
                return getattr(robot, name)(*args)
            coro = run()
            try:
                future = asyncio.run_coroutine_threadsafe(coro, loop)
            except RuntimeError:                         # 루프가 닫혔다
                coro.close()                             # 돌지 않을 코루틴을 닫는다. "never awaited" 경고가 남지 않는다
                self.send(503, {"error": "가짜 로봇을 끝내는 중입니다"})
                return
            try:
                self.send(200, future.result(timeout=2) or {"message": "받음"})
            except TimeoutError:
                self.send(503, {"error": "가짜 로봇이 응답하지 않습니다"})

        def send(self, code, payload, content_type="application/json; charset=utf-8"):
            body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            try:
                self.wfile.write(body)
            except ConnectionError:
                pass

        def local(self):
            expected = f"127.0.0.1:{self.server.server_port}"
            origin = self.headers.get("Origin")
            if self.headers.get("Host") == expected and origin in (None, "http://" + expected):
                return True
            self.send(403, {"error": "이 서버의 127.0.0.1 주소에서 요청하세요"})
            return False

        def do_GET(self):
            if not self.local():
                return
            url = urlsplit(self.path)
            if url.path == "/api/state":
                try:
                    since = int(parse_qs(url.query).get("log", ["0"])[0])
                except ValueError:
                    self.send(400, {"error": "log는 정수"})
                    return
                self.call("snapshot", since)
            elif url.path in files:
                path = files[url.path]
                self.send(200, path.read_bytes(), TYPES[path.suffix])
            else:
                self.send(404, {"error": "경로 없음"})

        def do_POST(self):
            # 본문은 먼저 끝까지 읽는다. 안 읽은 채 답하고 닫으면 연결이 끊겨(RST) 클라이언트가 답을 못 받는다
            try:
                self.connection.settimeout(3)
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length) if 0 < length <= 1 << 20 else b""
            except (ValueError, OSError):
                length, body = 0, b""
            if not self.local():
                return
            route = POSTS.get(urlsplit(self.path).path)
            if route is None:
                self.send(404, {"error": "경로 없음"})
                return
            fields, check, name = route
            try:
                if not 0 < length <= MAX_BODY or self.headers.get_content_type() != "application/json":
                    raise ValueError("작은 JSON 객체를 보내세요")
                payload = json.loads(body)
                if not isinstance(payload, dict) or set(payload) != fields:
                    raise ValueError("요청 필드: " + ", ".join(sorted(fields)))
                args = check(payload)
            except (ValueError, UnicodeError, OSError, RecursionError) as error:
                self.send(400, {"error": str(error)})
                return
            self.call(name, *args)

    return _Server(("127.0.0.1", port), Handler)


def arguments(argv=None):
    parser = argparse.ArgumentParser(prog="python -m fake_robot", description="가짜 로봇 + 3D 화면 (HCR-3A 흉내)")
    parser.add_argument("--plc-host", default="127.0.0.1", help="가짜 PLC 주소 (배선 JSON)")
    parser.add_argument("--wiring-port", type=int, default=5021, help="가짜 PLC 배선 포트")
    parser.add_argument("--http-port", type=int, default=8879, help="3D 화면 포트")
    args = parser.parse_args(argv)
    if not (0 <= args.wiring_port <= 65535 and 0 <= args.http_port <= 65535):
        parser.error("포트는 0~65535")
    return args


async def serve(args):
    wiring = WiringClient(args.plc_host, args.wiring_port)
    robot = FakeRobot(wiring)
    server = make_server(robot, asyncio.get_running_loop(), args.http_port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"가짜 로봇 3D 화면: http://127.0.0.1:{server.server_port}", flush=True)
    print(f"가짜 PLC 배선: {args.plc_host}:{args.wiring_port} (안 붙으면 1초마다 다시 접속)", flush=True)
    try:
        await asyncio.gather(wiring.run(), fixed_loop(robot.tick))
    finally:
        await asyncio.to_thread(server.shutdown)
        server.server_close()
