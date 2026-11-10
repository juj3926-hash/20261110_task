"""모니터 UI (docs/handoff/fake-plc.md 3.8). 표준 라이브러리 http.server, 127.0.0.1에서만 받는다.

요청 검사는 월요일 app.py 방식이다. Host와 Origin이 이 서버의 127.0.0.1 주소여야 하고, POST는 4 KB 이하의
JSON 객체이며 필드가 정확히 맞아야 한다. 엔진은 이벤트 루프 안에서만 만지므로 요청은 루프로 넘겨 처리하고,
응답 JSON도 루프 안에서 만든다(스캔이 값을 바꾸는 중에 읽지 않는다).

GET  /               monitor.html
GET  /api/state      화면이 0.25초마다 묻는 상태
GET  /api/diagram    렁 그림. 파일을 열 때마다 바뀐다(state의 opened로 안다)
POST /api/run {}     RUN (첫 스캔부터)            POST /api/stop {}    STOP
POST /api/open {"path": 문자열, "programs": 이름 목록 또는 null(모두)}
POST /api/force {"device": "P00004", "value": 0, 1 또는 null(해제)}
"""

import asyncio
import itertools
import json
import os
from concurrent.futures import TimeoutError as FutureTimeout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tools.xgwx import ladder_src
from tools.xgwx.build import program_devices
from tools.xgwx.ladder_src import Cmp

from fake_plc.diagram import name_of

PAGE = Path(__file__).resolve().parent / "monitor.html"
FIELDS = {"/api/run": set(), "/api/stop": set(), "/api/open": {"path", "programs"},
          "/api/force": {"device", "value"}}
_KIND = {0x06: "NO", 0x07: "NC", 0x08: "P", 0x0e: "OUT", 0x10: "SET", 0x11: "RST"}


def diagram(programs):
    """화면용 렁 그림과 강조 판정 목록.

    그림: [{"name", "rungs": [{"n", "rows", "comment", "items", "vlines"}]}]
      items: {"id", "row", "col", "span", "kind", "text"}. kind = H NO NC P CMP OUT SET RST BOX END ?(모름)
      vlines: [[행, 칸]…] — 그 칸의 오른쪽 경계에서 그 행과 아래 행을 잇는다
    판정: [(id, 방법, 디바이스 또는 Cmp)…]. 방법 = bit(켜져 있으면), nc(꺼져 있으면), cmp(참이면)"""
    ids, pictures, probes = itertools.count(), [], []
    for name, rungs in programs:
        drawn = []
        for n, rec in enumerate(rungs):
            rows = sorted(e.y for e in rec.elements if e.kind == 0x43)
            rung = {"n": n, "rows": len(rows), "comment": rec.comment, "items": [], "vlines": []}
            for e in rec.elements:
                tag, col = e.fields.get("tag"), (e.x - 1) // 3
                row = rows.index(e.y) if e.y in rows else 0
                if e.kind in (0x43, 0x3f) or tag in (0x01, 0x02):
                    continue                        # 행 머리, 렁 설명(comment로), 세로선 참조, 상자 부분
                if (tag, e.kind) == (0x00, 0x00):
                    rung["vlines"].append([row, (e.x - 3) // 3])
                    continue
                item = {"id": next(ids), "row": row, "col": col, "span": 1, "kind": "?", "text": name_of(e)}
                if e.kind == 0x02:
                    item.update(kind="H", text="", span=(e.fields["end_x"] - 1) // 3 - col + 1)
                elif tag == 0xff and e.kind in _KIND:
                    item.update(kind=_KIND[e.kind], text=e.fields["device"])
                    probes.append((item["id"], "nc" if e.kind == 0x07 else "bit", e.fields["device"]))
                elif (tag, e.kind) == (0x00, 0x22) and "text" in e.fields:
                    op, *args = e.fields["text"].split(",")
                    kind = "END" if op == "END" else "CMP" if op in ladder_src.COMPARE_OPS else "BOX"
                    item.update(kind=kind, span=1 if kind == "END" else 3, text=" ".join([op] + args))
                    if kind == "CMP" and len(args) == 2:
                        probes.append((item["id"], "cmp", Cmp(op, *args)))
                    elif op == "TON" and args:
                        probes.append((item["id"], "bit", args[0]))
                rung["items"].append(item)
            drawn.append(rung)
        pictures.append({"name": name, "rungs": drawn})
    return pictures, probes


def energized(plc, probes) -> list:
    """통전 강조할 id: 닫힌 접점(a·상승은 디바이스 1, b는 0), 참인 비교, 켜진 코일과 타이머.
    RUN한 적이 없으면 없다. 실행할 수 있는 프로그램만 Plc가 있으므로 피연산자는 이미 검사됐다."""
    if plc is None:
        return []
    test = {"bit": plc.bit, "nc": lambda dev: not plc.bit(dev), "cmp": plc._cmp}
    return [i for i, how, arg in probes if test[how](arg)]


def devices(engine) -> list:
    """디바이스 표: 래더에 쓰인 P, M, T, D. [[이름, 값, T면 누적 ms 아니면 None]…]"""
    plc = engine.plc
    used = {d for _, rungs in engine.programs for d in program_devices(rungs)
            if ladder_src.DEVICE_RE.fullmatch(d) and d[0] in "PMTD"}
    out = []
    for d in sorted(used, key=lambda d: ("PMTD".index(d[0]), d)):
        if d[0] == "D":
            out.append([d, engine.words.get(d, 0), None])
        else:
            acc = (plc.acc.get(d, 0) if plc else 0) if d[0] == "T" else None
            out.append([d, plc.bit(d) if plc else 0, acc])
    return out


class _Server(ThreadingHTTPServer):
    allow_reuse_address = os.name != "nt"  # 윈도우에서 켜면 두 번째 가짜 PLC도 같은 포트를 열어 요청을 나눠 받는다


def make_http(app, loop, port=8878):
    """모니터 HTTP 서버를 만든다(serve_forever는 부르는 쪽이). app: FakePlc, loop: 엔진이 도는 이벤트 루프."""
    cache = {}

    def view():
        """열린 파일의 (그림, 판정). 파일을 열 때마다 다시 만든다."""
        if cache.get("opened") != app.engine.opened:
            cache.update(opened=app.engine.opened, view=diagram(app.engine.programs))
        return cache["view"]

    def state():
        e = app.engine
        return {"running": e.running, "scans": e.scans, "path": str(e.path or ""), "opened": e.opened,
                "names": e.names, "chosen": [name for name, _ in e.programs],
                "problems": e.problems, "warnings": e.warnings, "forced": e.forced,
                "robot": len(app.wiring.writers), "ports": app.ports,
                "devices": devices(e), "on": energized(e.plc, view()[1]),
                "modbus": list(app.modbus_log)[::-1]}        # 최신이 위

    def run_plc(_):
        app.engine.run()                                     # 미지원이 있으면 ValueError → 400
        return {"message": "RUN"}

    def stop_plc(_):
        app.engine.stop()
        return {"message": "STOP"}

    def open_file(p):
        path, names = p["path"], p["programs"]
        if not isinstance(path, str) or not (
                names is None or isinstance(names, list) and all(isinstance(n, str) for n in names)):
            raise ValueError("path는 문자열, programs는 이름 목록 또는 null이어야 합니다")
        app.engine.open(path.strip().strip('"'), names)      # 탐색기 "경로로 복사"의 따옴표는 뗀다
        return {"message": "실행할 수 없음" if app.engine.problems else "열었음 (STOP)"}

    def force(p):
        dev, value = p["device"], p["value"]
        if not (isinstance(dev, str) and ladder_src.DEVICE_RE.fullmatch(dev) and dev[0] in "PM") \
                or value not in (0, 1, None):
            raise ValueError("device는 P·M 비트(예: P00004), value는 0, 1 또는 null이어야 합니다")
        if value is None:
            app.engine.release(dev)
        else:
            app.engine.forced[dev] = int(value)
        return {"message": "강제 %s = %s" % (dev, "해제" if value is None else int(value))}

    gets = {"/api/state": state, "/api/diagram": lambda: {"programs": view()[0]}}
    posts = {"/api/run": run_plc, "/api/stop": stop_plc, "/api/open": open_file, "/api/force": force}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

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
            except ConnectionError:                     # 브라우저가 먼저 끊었다 (WinError 10053 포함)
                pass

        def local_request(self):
            expected = "127.0.0.1:%d" % self.server.server_port
            origin = self.headers.get("Origin")
            if self.headers.get("Host") != expected or (origin is not None and origin != "http://" + expected):
                self.send(403, {"error": "이 서버의 127.0.0.1 주소에서 요청하세요"})
                return False
            return True

        def call(self, fn):
            """fn을 이벤트 루프에서 부르고 JSON까지 거기서 만든다. ValueError는 400.
            루프가 닫혔거나(끝내는 중) 4초 안에 답이 없으면 503이다. 연결을 그냥 끊지 않는다."""
            async def run():
                return json.dumps(fn(), ensure_ascii=False).encode()
            coro = run()
            try:
                future = asyncio.run_coroutine_threadsafe(coro, loop)
            except RuntimeError:                        # 루프가 닫혔다
                coro.close()                            # 돌지 않을 코루틴을 닫는다. "never awaited" 경고가 남지 않는다
                self.send(503, {"error": "가짜 PLC를 끝내는 중입니다"})
                return
            try:
                self.send(200, future.result(timeout=4))
            except FutureTimeout:
                self.send(503, {"error": "가짜 PLC가 응답하지 않습니다"})
            except ValueError as error:
                self.send(400, {"error": str(error)})
            except Exception as error:
                self.send(500, {"error": str(error)})

        def do_GET(self):
            if not self.local_request():
                return
            if self.path == "/":
                self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif self.path in gets:
                self.call(gets[self.path])
            else:
                self.send(404, {"error": "경로 없음"})

        def do_POST(self):
            if self.path not in posts:
                self.send(404, {"error": "경로 없음"})
                return
            try:
                self.connection.settimeout(3)
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 4096 or self.headers.get_content_type() != "application/json":
                    raise ValueError("작은 JSON 객체를 보내세요")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict) or set(payload) != FIELDS[self.path]:
                    raise ValueError("요청 필드가 올바르지 않습니다")
            except (ValueError, UnicodeError, OSError, RecursionError) as error:
                self.send(400, {"error": str(error)})
                return
            if not self.local_request():
                return
            action = posts[self.path]
            self.call(lambda: action(payload))

    return _Server(("127.0.0.1", port), Handler)
