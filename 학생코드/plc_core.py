"""PLC 감시·값 변경 프로그램의 공통 부품.

비동기판(plc_project.py)과 동기판(plc_project_sync.py)이 함께 쓴다.
이 모듈은 pymodbus·asyncio·threading을 쓰지 않는다. 두 판의 차이는
"통신 응답을 어떻게 기다리는가"에서만 생기도록, 설정·명령 해석·처리 원칙·
표시 문구는 모두 여기에 둔다.
"""

from __future__ import annotations

import argparse
import re
import time
import unicodedata
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol, TextIO

ADDRESS = 0  # PLC 쪽 D01000 (워드 쓰기 영역 시작 주소 기준 0)
WORD = "D01000"

VALUE_REASON = "0~65535 정수 또는 명령만 가능"
UNIT_REASON = "국번은 1~247 정수만 가능 (0 사용 안 함)"

_DIGITS = re.compile(r"[0-9]+")  # ASCII 숫자만 허용 (str.isdigit은 전각 숫자도 통과시킴)


# --------------------------------------------------------------------------- 설정


@dataclass(frozen=True)
class Settings:
    port: str = "COM3"
    baudrate: int = 9600
    bytesize: int = 8
    parity: str = "N"
    stopbits: int = 1
    unit: int = 1
    interval: float = 1.0
    timeout: float = 2.0
    retries: int = 0


def _unit_arg(text: str) -> int:
    value = int(text)
    if not 1 <= value <= 247:
        raise argparse.ArgumentTypeError(UNIT_REASON)
    return value


def _positive_float(text: str) -> float:
    value = float(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("0보다 커야 함")
    return value


def _non_negative_int(text: str) -> int:
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError("0 이상이어야 함")
    return value


def parse_settings(argv: list[str] | None = None) -> Settings:
    """명령행 인자를 Settings로 바꾼다. 잘못된 값이면 SystemExit."""
    d = Settings()
    p = argparse.ArgumentParser(description="PLC D01000 감시·값 변경 (Modbus RTU)")
    p.add_argument("--port", default=d.port, help=f"COM 포트 (기본 {d.port})")
    p.add_argument("--unit", type=_unit_arg, default=d.unit, help=f"대상 국번 1~247 (기본 {d.unit})")
    p.add_argument("--interval", type=_positive_float, default=d.interval, help=f"감시 주기 초 (기본 {d.interval})")
    p.add_argument("--timeout", type=_positive_float, default=d.timeout, help=f"응답 대기 초 (기본 {d.timeout})")
    p.add_argument("--retries", type=_non_negative_int, default=d.retries, help=f"재시도 횟수 (기본 {d.retries})")
    a = p.parse_args(argv)
    return Settings(port=a.port, unit=a.unit, interval=a.interval, timeout=a.timeout, retries=a.retries)


def describe_settings(s: Settings) -> str:
    return (
        f"{s.port} · {s.baudrate} · {s.bytesize}{s.parity}{s.stopbits} · RTU | 국번 {s.unit} | "
        f"감시 {s.interval:.1f}초 | timeout {s.timeout:.1f}초 | retries {s.retries}"
    )


def fmt_time(wall: float) -> str:
    """벽시계 시각(time.time())을 로컬 HH:MM:SS.mmm으로."""
    t = time.localtime(wall)
    ms = int(round((wall - int(wall)) * 1000))
    if ms == 1000:  # 반올림으로 다음 초가 된 경우
        return fmt_time(float(int(wall) + 1))
    return f"{t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}.{ms:03d}"


# --------------------------------------------------------------------------- 명령


@dataclass(frozen=True)
class Command:
    kind: str  # write unit status help quit reject empty
    value: int | None = None
    text: str = ""
    reason: str = ""


def parse_command(line: str) -> Command:
    text = line.strip()
    if not text:
        return Command("empty")
    words = text.split()
    head = words[0].lower()
    if head == "unit":
        n = _small_int(words[1]) if len(words) == 2 else None
        if n is not None and 1 <= n <= 247:
            return Command("unit", n, text)
        return Command("reject", None, text, UNIT_REASON)
    if len(words) == 1:
        if head == "status":
            return Command("status", None, text)
        if head == "help":
            return Command("help", None, text)
        if head in ("q", "quit"):
            return Command("quit", None, text)
        n = _small_int(text)
        if n is not None and n <= 65535:
            return Command("write", n, text)
    return Command("reject", None, text, VALUE_REASON)


def _small_int(s: str) -> int | None:
    """ASCII 10진 숫자이고 앞자리 0을 뺀 길이가 5자리 이하일 때만 int로 바꾼다.
    (아주 긴 숫자를 int()에 넘기면 4300자리 제한 ValueError로 프로그램이 끝나므로 먼저 거른다)"""
    if not _DIGITS.fullmatch(s):
        return None
    digits = s.lstrip("0") or "0"  # int()의 자릿수 제한은 앞자리 0도 센다
    return int(digits) if len(digits) <= 5 else None


# --------------------------------------------------------------------------- 통신 결과


@dataclass(frozen=True)
class CommResult:
    ok: bool
    value: int | None
    error: str  # 성공이면 ""
    elapsed: float  # 통신 함수 호출부터 반환까지 걸린 초
    at: float  # 결과를 얻은 벽시계 시각


# --------------------------------------------------------------------------- 상태

Entry = tuple[str, str]  # (태그, 내용)

KIND_KO = {"read": "읽기", "write": "쓰기", "verify": "확인 읽기"}

NOTICE_AFTER = 0.5  # 통신이 이 시간을 넘기면 대기 안내 시작
NOTICE_EVERY = 0.5  # 대기 안내 간격


@dataclass
class Observation:
    value: int
    at: float  # 관측 시각(벽시계)


@dataclass
class WriteRequest:
    id: int
    value: int
    unit: int  # 접수 시점의 대상 국번에 묶임
    accepted_at: float
    status: str = "대기"  # 대기 처리중 성공 실패 취소(종료) 취소(중단)


@dataclass
class Comm:
    kind: str  # read write verify
    unit: int
    request_id: int | None
    started: float  # monotonic
    ended: float | None = None

    @property
    def label(self) -> str:
        rid = f"(#{self.request_id})" if self.request_id is not None else ""
        return f"국번 {self.unit} {KIND_KO[self.kind]}{rid}"


class PlcState:
    """두 판이 공유하는 상태. 시각은 모두 인자로 받는다(시험에서 시각을 정할 수 있게)."""

    def __init__(self, settings: Settings, now_mono: float):
        self.settings = settings
        self.unit = settings.unit
        self.queue: deque[WriteRequest] = deque()
        self.observations: dict[int, Observation] = {}
        self.failures: dict[int, int] = {}
        self.in_flight: Comm | None = None
        self.last_comm: Comm | None = None
        self.next_read_at = now_mono  # 시작 직후 첫 읽기는 바로
        self.quitting = False
        self.next_id = 1
        self._last_notice: float | None = None

    # ---- 관측 표시
    def describe_unit(self, unit: int) -> str:
        obs = self.observations.get(unit)
        if obs is None:
            return f"국번 {unit}: 미관측"
        n = self.failures.get(unit, 0)
        if n == 0:
            return f"국번 {unit}: {obs.value} @{fmt_time(obs.at)}"
        return f"국번 {unit}: 마지막 정상 관측 {obs.value} @{fmt_time(obs.at)} (이후 실패 {n}회)"

    def summary(self, unit: int) -> str:
        text = self.describe_unit(unit)
        if unit not in self.observations:
            others = [(u, o) for u, o in self.observations.items() if u != unit]
            if others:
                u2, o2 = max(others, key=lambda item: item[1].at)
                text += f" | 마지막 정상 관측: 국번 {u2} = {o2.value} @{fmt_time(o2.at)}"
        return text

    # ---- 진행 중 통신
    def comm_status(self, now_mono: float) -> str:
        c = self.in_flight
        if c is None:
            return "통신 없음"
        return f"{c.label} 중 {now_mono - c.started:.1f}초 경과"

    def begin_comm(self, kind: str, unit: int, request_id: int | None, now_mono: float) -> None:
        self.in_flight = Comm(kind, unit, request_id, now_mono)
        self._last_notice = None

    def end_comm(self, now_mono: float) -> Comm:
        c = self.in_flight
        assert c is not None, "end_comm without begin_comm"
        c.ended = now_mono
        self.in_flight = None
        self.last_comm = c
        # 현재 대상 국번을 읽은 경우에만 다음 감시를 주기 뒤로 잡는다. 통신 중에 unit을 바꿨다면
        # 그때 잡힌 "바로 읽기"를 유지한다.
        if c.kind in ("read", "verify") and c.unit == self.unit:
            self.next_read_at = now_mono + self.settings.interval
        return c

    def wait_notice(self, now_mono: float) -> Entry | None:
        c = self.in_flight
        if c is None:
            return None
        elapsed = now_mono - c.started
        if elapsed < NOTICE_AFTER:
            return None
        if self._last_notice is not None and now_mono - self._last_notice < NOTICE_EVERY:
            return None
        self._last_notice = now_mono
        return (
            "대기",
            f"국번 {c.unit} {KIND_KO[c.kind]} 응답 대기 {elapsed:.1f}/{self.settings.timeout:.1f}초 | "
            f"{self.summary(self.unit)}",
        )


def _record_read(state: PlcState, unit: int, res: CommResult) -> None:
    """성공한 읽기만 관측값을 만든다. 실패는 실패 횟수만 센다."""
    if res.ok:
        state.observations[unit] = Observation(res.value, res.at)
        state.failures[unit] = 0
    else:
        state.failures[unit] = state.failures.get(unit, 0) + 1


def apply_read_result(state: PlcState, unit: int, res: CommResult) -> list[Entry]:
    _record_read(state, unit, res)
    if res.ok:
        return [("관측", f"국번 {unit} {WORD}={res.value} @{fmt_time(res.at)}")]
    return [("읽기실패", f"국번 {unit} {res.error} ({res.elapsed:.2f}초) — 새 관측 없음 | {state.summary(unit)}")]


# --------------------------------------------------------------------------- 명령 적용·대기열

HELP_LINES = [
    "숫자(0~65535) + Enter: D01000 값 변경 요청 — FIFO 대기열, 접수 시점의 국번으로 보냄",
    "unit N: 읽기 대상 국번 변경 (1~247), 바꾼 직후 바로 읽음",
    "status: 통신 없이 현재 상태 표시",
    "help: 이 도움말",
    "q / quit: 진행 중 통신이 끝나면 종료 (Ctrl+C는 즉시 중단)",
]


@dataclass(frozen=True)
class Action:
    kind: str  # write read
    unit: int
    request: WriteRequest | None = None


def startup_lines(settings: Settings) -> list[Entry]:
    return [
        ("시작", "설정: " + describe_settings(settings)),
        ("시작", "명령: 숫자(0~65535)=쓰기 · unit N · status · help · q"),
    ]


def _status_lines(state: PlcState, now_mono: float) -> list[Entry]:
    lines = [f"대상 국번 {state.unit} | 통신 상태: {state.comm_status(now_mono)} | 대기 {len(state.queue)}건"]
    units = sorted(set(state.observations) | set(state.failures) | {state.unit})
    lines += [state.describe_unit(u) for u in units]
    lines += [f"대기 #{r.id} {WORD}←{r.value} (국번 {r.unit})" for r in state.queue]
    return [("상태", line) for line in lines]


def apply_command(state: PlcState, cmd: Command, now_mono: float, now_wall: float) -> list[Entry]:
    """명령을 접수한다. 통신은 하지 않는다."""
    if cmd.kind == "write":
        req = WriteRequest(state.next_id, cmd.value, state.unit, now_wall)
        state.next_id += 1
        state.queue.append(req)
        return [
            (
                "접수",
                f"#{req.id} {WORD}←{req.value} (국번 {req.unit}) | 통신 상태: {state.comm_status(now_mono)} | "
                f"대기 {len(state.queue)}건",
            )
        ]
    if cmd.kind == "unit":
        old, state.unit = state.unit, cmd.value
        state.next_read_at = now_mono  # 바꾼 직후 바로 읽음
        return [("설정", f"대상 국번 {old} → {state.unit} | {state.summary(state.unit)}")]
    if cmd.kind == "status":
        return _status_lines(state, now_mono)
    if cmd.kind == "help":
        return [("상태", line) for line in HELP_LINES]
    if cmd.kind == "quit":
        state.quitting = True
        return [("종료", "종료 요청 접수 — 진행 중 통신이 끝나면 종료")]
    if cmd.kind == "reject":
        return [("거부", f"'{cmd.text}' — {cmd.reason}")]
    return []  # empty


def take_next_action(state: PlcState, now_mono: float) -> Action | None:
    """다음 통신 작업 하나: 대기열의 쓰기가 먼저, 없으면 감시 시각이 된 읽기."""
    if state.quitting:
        return None
    if state.queue:
        req = state.queue.popleft()
        req.status = "처리중"
        return Action("write", req.unit, req)
    if now_mono >= state.next_read_at:
        return Action("read", state.unit)
    return None


def apply_write_result(state: PlcState, req: WriteRequest, res: CommResult) -> list[Entry]:
    if res.ok:
        req.status = "성공"
        return [("쓰기", f"#{req.id} 성공 (국번 {req.unit}, {WORD}←{req.value}, {round(res.elapsed * 1000)}ms)")]
    req.status = "실패"
    return [("쓰기", f"#{req.id} 실패 (국번 {req.unit}, {res.error} {res.elapsed:.2f}초)")]


def apply_verify_result(state: PlcState, req: WriteRequest, res: CommResult) -> list[Entry]:
    _record_read(state, req.unit, res)
    if not res.ok:
        return [("확인", f"#{req.id} 확인 읽기 실패 ({res.error} {res.elapsed:.2f}초) — 쓰기 성공, 반영 미확인")]
    if res.value == req.value:
        return [("확인", f"#{req.id} 읽은 값 {res.value} = 요청값 → 반영 확인 @{fmt_time(res.at)}")]
    return [("확인", f"#{req.id} 불일치 → 읽은 값 {res.value} (요청값 {req.value}) @{fmt_time(res.at)}")]


def cancel_pending(state: PlcState, reason: str) -> list[Entry]:
    entries = []
    while state.queue:
        req = state.queue.popleft()
        req.status = f"취소({reason})"
        entries.append(("종료", f"#{req.id} 취소({reason}) {WORD}←{req.value} (국번 {req.unit})"))
    return entries


# --------------------------------------------------------------------------- 키 입력

KEY_GAP = 0.2  # 직전 키 확인에서 이보다 오래 지났는데 키가 쌓여 있으면 "입력 지연"


class KeySource(Protocol):
    """msvcrt 모듈과 같은 모양. 실제 실행에서는 msvcrt를 그대로 넘긴다."""

    def kbhit(self) -> bool: ...

    def getwch(self) -> str: ...


@dataclass(frozen=True)
class KeyDelay:
    count: int  # 이번에 꺼낸 키 수
    since_mono: float  # 직전 키 확인 시각
    until_mono: float  # 이번 키 확인 시각
    since_wall: float
    until_wall: float


class KeyReader:
    """쌓인 키를 차단 없이 모두 꺼내 한 줄 편집기로 처리한다."""

    def __init__(self, source: KeySource, gap: float = KEY_GAP):
        self.source = source
        self.gap = gap
        self.buffer = ""
        self._last_mono: float | None = None
        self._last_wall: float | None = None
        self._skip_next = False  # 특수키(\x00/\xe0) 다음 글자 버리기

    def poll(self, now_mono: float, now_wall: float) -> tuple[list[str], KeyDelay | None]:
        lines: list[str] = []
        count = 0
        while self.source.kbhit():
            ch = self.source.getwch()
            count += 1
            if self._skip_next:
                self._skip_next = False
            elif ch in ("\x00", "\xe0"):
                self._skip_next = True
            elif ch == "\r":
                lines.append(self.buffer)
                self.buffer = ""
            elif ch == "\x08":
                self.buffer = self.buffer[:-1]
            elif ch == "\x1b":
                self.buffer = ""
            elif ch.isprintable():
                self.buffer += ch
        delay = None
        if count and self._last_mono is not None and now_mono - self._last_mono > self.gap:
            delay = KeyDelay(count, self._last_mono, now_mono, self._last_wall, now_wall)
        self._last_mono, self._last_wall = now_mono, now_wall
        return lines, delay


def format_input_delay(state: PlcState, d: KeyDelay) -> Entry:
    msg = f"키 {d.count}개가 {fmt_time(d.since_wall)}~{fmt_time(d.until_wall)} 사이에 입력되어 이제 처리됨"
    c = state.last_comm
    if c is not None and c.ended is not None and c.ended > d.since_mono:
        msg += f" (그 사이 국번 {c.unit} {KIND_KO[c.kind]} {c.ended - c.started:.2f}초)"
    return ("입력지연", msg)


# --------------------------------------------------------------------------- 화면·로그 파일


def _cells(text: str) -> int:
    """터미널에서 차지하는 칸 수 (한글 등 넓은 글자는 2칸)."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


class Display:
    """로그 줄과 맨 아래 입력 줄을 함께 그린다. ANSI 없이 \r과 공백으로 지운다."""

    PROMPT = "> "

    def __init__(self, out: TextIO, log_file: TextIO | None = None):
        self.out = out
        self.log_file = log_file
        self.lines: list[str] = []
        self._input = ""
        self._drawn = 0  # 현재 화면에 그려진 입력 줄의 칸 수

    def _clear_input(self) -> None:
        if self._drawn:
            self.out.write("\r" + " " * self._drawn + "\r")
            self._drawn = 0

    def _draw_input(self) -> None:
        text = self.PROMPT + self._input
        self.out.write(text)
        self.out.flush()
        self._drawn = _cells(text)

    def log(self, tag: str, msg: str, wall: float | None = None) -> str:
        line = f"{fmt_time(time.time() if wall is None else wall)} [{tag}] {msg}"
        self.lines.append(line)
        self._clear_input()
        self.out.write(line + "\n")
        self._draw_input()
        if self.log_file is not None:
            self.log_file.write(line + "\n")
            self.log_file.flush()
        return line

    def show_input(self, buffer: str) -> None:
        if buffer == self._input and self._drawn:
            return
        self._clear_input()
        self._input = buffer
        self._draw_input()

    def finish(self) -> None:
        """종료 시 입력 줄을 지워 셸 프롬프트가 깨끗한 줄에서 시작하게 한다."""
        self._clear_input()
        self.out.flush()


def open_log_file(kind: str, directory: Path, now: datetime) -> TextIO:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{kind}_{now:%Y%m%d_%H%M%S}.log"
    return open(path, "w", encoding="utf-8", buffering=1)


def shutdown_entries(state: PlcState, interrupted: str | None) -> list[Entry]:
    """종료 정리 기록. interrupted가 None이면 q로 끝난 정상 종료."""
    if interrupted is None:
        return cancel_pending(state, "종료")
    msg = interrupted
    if state.in_flight is not None:
        msg += f" — 진행 중이던 {state.in_flight.label} 중단"
    return [("종료", msg)] + cancel_pending(state, "중단")
