"""웹 서버, 실행 인자, 로그 파일 (spec 7.4). 제공 부분이다.

- 서버는 표준 라이브러리 ThreadingHTTPServer다. 요청마다 스레드 하나가 받는다.
- 상태 읽기와 버튼 처리는 모두 asyncio 루프에서 한다. 요청 스레드는 run_coroutine_threadsafe로 넘기고 기다리기만 한다.
- 요청 검사는 월요일 app.py와 같다. 127.0.0.1의 Host·Origin, 4 KB 이하 JSON, 정해진 필드만 받는다.
"""

import argparse
import asyncio
import json
import math
import os
import threading
import time
from collections import deque
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from plc_core import fmt_time, open_log_file
from picknplace_full.link import Event, PlcLink
from picknplace_full.sequence import BUTTONS, Sequencer
from picknplace_full.transport import AutoTransport

WEB = Path(__file__).resolve().parent / "web"
LOGS = Path(__file__).resolve().parents[1] / "logs"
FILES = {"/": ("index.html", "text/html; charset=utf-8"), "/ui.js": ("ui.js", "text/javascript; charset=utf-8")}
FIELDS = {"/api/press": {"button"}, "/api/code": {"code"}, "/api/rescan": set()}
SEQUENCE = ("running", "step", "stop_requested", "auto_maybe_on", "last_result")
WAIT = 10.0  # 요청 하나의 응답 한도(초). 명령 하나는 우편함 확인과 처리 확인에 각각 2초까지 걸린다


class EventLog:
    """이벤트를 화면용 deque(최근 200개)와 로그 파일에 함께 쓴다. 루프 스레드에서만 부른다.

    파일은 open(folder)로 연다. folder/PICKNPLACE_FULL_YYYYMMDD_HHMMSS.log(UTF-8)이고, 한 줄이 `HH:MM:SS.mmm [태그] 내용`이다.
    """

    def __init__(self):
        self.events: deque[Event] = deque(maxlen=200)
        self.file = None

    def open(self, folder: Path):
        """로그 파일을 열어 돌려준다. 열기 전과 닫은 뒤의 줄은 화면에만 남는다."""
        self.file = open_log_file("PICKNPLACE_FULL", folder, datetime.now())
        return self.file

    def write(self, e: Event) -> None:
        """link.on_event 자리."""
        self.events.append(e)
        if self.file is not None and not self.file.closed:
            self.file.write("%s [%s] %s\n" % (fmt_time(e.at), e.tag, e.text))

    def log(self, tag: str, text: str) -> None:
        """Sequencer의 log 자리. 지금 시각으로 쓴다."""
        self.write(Event(time.time(), tag, text))


class _Server(ThreadingHTTPServer):
    allow_reuse_address = os.name != "nt"  # 윈도우에서 켜면 두 번째 프로그램도 같은 포트를 열어 요청을 나눠 받는다


def make_server(link: PlcLink, seq: Sequencer, loop: asyncio.AbstractEventLoop, port=8877,
                events=()) -> ThreadingHTTPServer:
    """127.0.0.1:port의 서버를 만든다. serve_forever는 부르는 쪽이 스레드에서 돌린다."""

    async def status() -> dict:
        s, t = link.status, link.transport
        auto = isinstance(t, AutoTransport)
        return {
            "status": None if s is None else {
                "raw": s.raw, "mailbox": s.mailbox, "robot": s.robot, "gripper": s.gripper, "plc": s.plc,
                "valid": s.valid, "text": s.text(), "at": fmt_time(s.at)},
            "comm_ok": link.comm_ok,
            "target": "auto · %s" % (t.port or "포트 찾는 중") if auto else t.name,
            "ports": [list(r) for r in t.results] if auto else [],
            "sequence": {k: getattr(seq, k) for k in SEQUENCE},
            "buttons": seq.buttons(),
            "events": [{"at": fmt_time(e.at), "tag": e.tag, "text": e.text} for e in events],
        }

    async def rescan() -> str:
        """[다시 찾기]는 포트를 못 찾았을 때 쓴다(spec 6.2). 통신 중이거나 자동 순서 중이면 쓰던 포트를 닫지 않는다."""
        t = link.transport
        if not isinstance(t, AutoTransport):
            return "포트 찾기는 --target auto일 때만 합니다"
        if seq.running:
            return "자동 순서 중에는 다시 찾지 않습니다"
        if link.comm_ok and t.port:
            return "%s 포트로 통신 중이라 다시 찾지 않습니다" % t.port
        return "포트를 찾았습니다: %s" % t.port if await link.rescan() else "응답하는 포트가 없습니다"

    async def reply(coro) -> dict:
        return {"message": await coro}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # 요청마다 stderr에 남기지 않는다. 기록은 로그 창과 파일이 맡는다

        def send(self, code: int, payload, content_type="application/json; charset=utf-8") -> None:
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
                pass  # 브라우저가 먼저 끊었다. 이미 처리한 요청은 되돌리지 않는다

        def local(self) -> bool:
            """Host와 Origin이 이 서버의 127.0.0.1 주소인지 본다. 아니면 403을 보낸다."""
            expected = "127.0.0.1:%d" % self.server.server_port
            if self.headers.get("Host") == expected and self.headers.get("Origin") in (None, "http://" + expected):
                return True
            self.send(403, {"message": "이 서버의 127.0.0.1 주소에서 요청하세요"})
            return False

        def call(self, make) -> None:
            """make()가 만드는 코루틴을 루프에서 돌리고 결과를 보낸다.

            WAIT가 지나도 코루틴은 멈추지 않는다(보낸 명령은 되돌릴 수 없다). 루프가 닫혔으면(끝내는 중) 503이다.
            """
            async def run():
                return await make()  # 코루틴은 루프에서 만든다. 넘기지 못하면 run 하나만 닫으면 된다

            coro = run()
            try:
                future = asyncio.run_coroutine_threadsafe(coro, loop)
            except RuntimeError:  # 루프가 닫혔다
                coro.close()  # 돌지 않을 코루틴을 닫는다. "never awaited" 경고가 남지 않는다
                self.send(503, {"message": "프로그램을 끝내는 중입니다"})
                return
            try:
                self.send(200, future.result(WAIT))
            except FutureTimeout:
                self.send(503, {"message": "응답 미확인: 처리는 계속될 수 있습니다. 처리 여부가 미확인입니다. 새 상태와 로그를 확인하세요"})
            except Exception as e:
                self.send(500, {"message": "서버 오류: %s" % e})

        def do_GET(self):
            if not self.local():
                return
            if self.path == "/api/status":
                self.call(status)
            elif self.path in FILES:
                name, kind = FILES[self.path]
                self.send(200, (WEB / name).read_bytes(), kind)
            else:
                self.send(404, {"message": "경로 없음"})

        def do_POST(self):
            if self.path not in FIELDS:
                self.send(404, {"message": "경로 없음"})
                return
            try:
                self.connection.settimeout(3)
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 4096 or self.headers.get_content_type() != "application/json":
                    raise ValueError("4 KB 이하의 JSON 객체를 보내세요")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict) or set(payload) != FIELDS[self.path]:
                    raise ValueError("필드는 %s여야 합니다" % sorted(FIELDS[self.path]))
                if self.path == "/api/press" and payload["button"] not in BUTTONS:
                    raise ValueError("button은 %s 중 하나입니다" % ", ".join(BUTTONS))
                if self.path == "/api/code" and not (type(payload["code"]) is int and 1 <= payload["code"] <= 65535):
                    raise ValueError("code는 1~65535 정수입니다")
            except (ValueError, UnicodeError, OSError, RecursionError) as e:
                self.send(400, {"message": str(e)})
                return
            if not self.local():
                return
            calls = {"/api/press": lambda: reply(seq.press(payload["button"])),
                     "/api/code": lambda: reply(seq.send_code(payload["code"])),
                     "/api/rescan": lambda: reply(rescan())}
            self.call(calls[self.path])

    return _Server(("127.0.0.1", port), Handler)


def _positive(text: str) -> float:
    """--interval, --speed. 0보다 큰 유한한 수만 받는다(inf, nan은 거절)."""
    value = float(text)
    if not (math.isfinite(value) and value > 0):
        raise argparse.ArgumentTypeError("0보다 큰 유한한 수여야 함")
    return value


def arguments(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m picknplace_full", description="PICKNPLACE_FULL PC 프로그램 (버튼 화면)")
    p.add_argument("--target", default="auto", help="auto, serial:COMn, tcp:HOST:PORT, model (기본 auto)")
    p.add_argument("--http-port", type=int, default=8877, help="화면 포트 (기본 8877, 0이면 빈 포트)")
    p.add_argument("--interval", type=_positive, default=0.2, help="읽기 주기 초 (기본 0.2)")
    p.add_argument("--speed", type=_positive, default=1.0, help="model 배속 (기본 1)")
    args = p.parse_args(argv)
    if not 0 <= args.http_port <= 65535:
        p.error("--http-port는 0~65535입니다")
    return args


async def serve(args: argparse.Namespace) -> None:
    """통신 작업과 서버를 띄우고 멈출 때(Ctrl+C)까지 돈다. 멈출 때는 자동 순서, 통신, 서버, 로그 파일 순으로 닫는다."""
    link = PlcLink(args.target, interval=args.interval, speed=args.speed)  # 대상이 틀리면 ValueError
    log = EventLog()
    link.on_event(log.write)
    seq = Sequencer(link, log=log.log)
    server = make_server(link, seq, asyncio.get_running_loop(), args.http_port, log.events)  # 포트가 쓰이는 중이면 OSError
    with server, log.open(LOGS):  # 포트를 연 뒤에 로그 파일을 연다. 시작에 실패하면 빈 파일이 남지 않는다
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            await link.start()
            print("http://127.0.0.1:%d · target=%s" % (server.server_port, args.target), flush=True)
            await asyncio.Event().wait()
        finally:
            await seq.close()  # 먼저 멈춘다. 그대로 두면 다음 명령(예: 9992)을 보내고, 닫힌 통신을 시간 초과로 적는다
            await link.close()  # 서버가 멈추는 동안 눌린 버튼은 보내지 않고 미확인(통신 종료)이 된다
            await asyncio.to_thread(server.shutdown)
