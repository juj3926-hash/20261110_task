"""통신 부품 (spec 6절). 통신 작업 하나가 연결, 읽기, 명령 쓰기를 모두 맡는다."""

import asyncio
import contextlib
import time
import traceback
from collections import deque
from dataclasses import dataclass
from typing import Callable

from picknplace_full.status import ESTOP, Status, decode
from picknplace_full.transport import AutoTransport, Transport, make_transport

CLOSED = "통신 종료"


@dataclass(frozen=True)
class Event:
    at: float
    tag: str
    text: str


@dataclass(frozen=True)
class SendResult:
    result: str  # "접수", "거부", "미확인"
    code: int
    reason: str
    status: Status | None
    elapsed: float


def _change_text(old: Status | None, new: Status) -> str:
    """상태 이벤트 글. 예: 0002 → 0001 (동작 중 · 열림 · 정상) · 우편함 9991"""
    text = "%s → %04d (%s)" % ("----" if old is None else "%04d" % old.raw, new.raw, new.text())
    if new.mailbox != (0 if old is None else old.mailbox):
        text += " · 우편함 %d" % new.mailbox
    return text


class PlcLink:
    """PLC와 통신하는 유일한 작업. 명령은 대기열에서 하나씩 처리한다(spec 6.3, 6.4)."""

    def __init__(self, target: str | Transport, interval=0.2, fast_interval=0.05, timeout=2.0, speed=1.0):
        self.transport = make_transport(target, speed) if isinstance(target, str) else target
        self.interval, self.fast_interval, self.timeout = interval, fast_interval, timeout
        self.status: Status | None = None  # 마지막 정상 읽기. 실패해도 지우지 않는다
        self.comm_ok = False  # 마지막 읽기 성공 여부
        self._callbacks: list[Callable[[Event], None]] = []
        self._queue = deque()  # (코드, Future). 코드 None은 포트 다시 찾기
        self._current = None  # 처리 중인 (코드, Future, 시작 시각)
        self._wake = asyncio.Event()  # 대기열에 넣으면 쉬던 통신 작업을 깨운다
        self._fresh = asyncio.Condition()  # 정상 읽기마다 알린다
        self._failing = False  # 통신 실패 이벤트를 남겼고 아직 회복 전
        self._stopped: str | None = None  # 멈춘 이유: 닫힘 또는 통신 작업 고장
        self._task = None
        if isinstance(self.transport, AutoTransport):
            self.transport.emit = self._emit

    def on_event(self, cb: Callable[[Event], None]) -> None:
        """로그용 이벤트를 받는다. cb가 예외를 내면 stderr에 남기고 통신은 계속한다."""
        self._callbacks.append(cb)

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run())
        self._task.add_done_callback(self._ended)

    async def close(self) -> None:
        """통신 작업을 멈춘다. 남은 send는 미확인, wait는 None으로 끝내고 연결을 닫는다."""
        self._stopped = CLOSED
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        self._fail_pending(CLOSED)
        async with self._fresh:
            self._fresh.notify_all()
        await self.transport.close()

    async def send(self, code: int) -> SendResult:
        """명령 하나를 보내고 PLC 처리(접수·거부·미확인)까지 기다린다. 9995는 대기열 맨 앞에 넣는다."""
        if type(code) is not int or not 1 <= code <= 65535:
            raise ValueError("명령 코드는 1~65535 정수입니다: %r" % (code,))
        if self._stopped:
            return SendResult("미확인", code, self._stopped, self.status, 0.0)
        return await self._put(code, front=code == ESTOP)

    async def rescan(self) -> bool:
        """COM 포트를 다시 찾는다. AutoTransport일 때만 동작한다."""
        if not isinstance(self.transport, AutoTransport) or self._stopped:
            return False
        return await self._put(None)

    async def wait(self, pred: Callable[[Status], bool], timeout: float) -> Status | None:
        """wait를 부른 뒤 끝난 정상 읽기마다 pred를 본다. 만족한 Status, 한도가 지나거나 닫히면 None."""
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(timeout), self._fresh:
                while not self._stopped:
                    await self._fresh.wait()
                    if not self._stopped and pred(self.status):
                        return self.status
        return None

    def _emit(self, tag: str, text: str) -> None:
        event = Event(time.time(), tag, text)
        for cb in self._callbacks:
            try:
                cb(event)
            except Exception:  # 로그 쪽 고장으로 통신(즉시 정지 포함)을 멈추지 않는다
                traceback.print_exc()

    def _ended(self, task: asyncio.Task) -> None:
        """통신 작업이 예외로 끝났다. 남은 명령을 미확인으로 끝내고 통신 이벤트를 하나 남긴다."""
        if task.cancelled() or task.exception() is None:
            return
        self._stopped = "통신 작업 멈춤: %r" % task.exception()
        self.comm_ok = False
        self._fail_pending(self._stopped)
        self._emit("통신", self._stopped)
        traceback.print_exception(task.exception())

    def _fail_pending(self, reason: str) -> None:
        """대기열과 처리 중인 명령을 미확인으로 끝낸다. 처리 중이던 명령은 결과 이벤트도 남긴다."""
        current, queued = self._current, list(self._queue)
        self._current = None
        self._queue.clear()
        for code, fut in queued:
            if not fut.done():
                fut.set_result(False if code is None else SendResult("미확인", code, reason, self.status, 0.0))
        if current:
            code, fut, t0 = current
            result = False if code is None else self._result("미확인", code, t0, reason)
            if not fut.done():
                fut.set_result(result)

    def _put(self, code: int | None, front=False) -> asyncio.Future:
        fut = asyncio.get_running_loop().create_future()
        (self._queue.appendleft if front else self._queue.append)((code, fut))
        self._wake.set()
        return fut

    async def _run(self) -> None:
        await self.transport.connect()
        await self._read()  # 첫 읽기는 바로 한다
        while True:
            if self._queue:
                await self._next()
                continue
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), self.interval)
            if not self._queue:
                await self._read()

    async def _next(self) -> None:
        code, fut = self._queue.popleft()
        if fut.done():  # 기다리던 쪽이 취소했으면 보내지 않는다
            return
        t0 = time.monotonic()
        self._current = (code, fut, t0)
        result = await (self.transport.rescan() if code is None else self._command(code, t0))
        self._current = None
        if not fut.done():
            fut.set_result(result)

    async def _read(self) -> Status | None:
        """한 번 읽는다. 정상이면 Status, 실패하면 None."""
        try:
            raw, mailbox = await self.transport.read()
        except Exception as e:
            self.comm_ok = False
            if not self._failing:
                self._failing = True
                self._emit("통신", "읽기 실패: %s" % e)
            if isinstance(self.transport, AutoTransport) and self.transport.lost():
                await self.transport.rescan()
            return None
        old, self.status, self.comm_ok = self.status, decode(raw, mailbox, time.time()), True
        if self._failing:
            self._failing = False
            self._emit("통신", "읽기 회복")
        if old is None or (old.raw, old.mailbox) != (raw, mailbox):
            self._emit("상태", _change_text(old, self.status))
        async with self._fresh:
            self._fresh.notify_all()
        return self.status

    async def _poll(self, deadline: float) -> Status | None:
        """fast_interval마다 읽어 우편함이 0인 Status를 돌려준다. 한도가 지나면 None."""
        while time.monotonic() < deadline:
            await asyncio.sleep(self.fast_interval)
            s = await self._read()
            if s is not None and s.mailbox == 0:
                return s
        return None

    async def _command(self, code: int, t0: float) -> SendResult:
        """우편함 규칙(spec 6.4). 쓰기는 한 번뿐이고 자동으로 다시 보내지 않는다."""
        s = self.status
        if (s is None or s.mailbox) and await self._poll(t0 + self.timeout) is None:
            reason = "이전 명령이 처리되지 않음" if self.comm_ok else "통신 실패로 우편함 확인 안 됨"
            return self._result("미확인", code, t0, reason)
        self._emit("보냄", "%d" % code)
        try:
            await self.transport.write_command(code)
        except Exception as e:  # 응답만 잃고 실제로는 쓰였을 수 있다
            return self._result("미확인", code, t0, "쓰기 실패: %s" % e)
        s = await self._poll(time.monotonic() + self.timeout)
        if s is None:
            return self._result("미확인", code, t0, "PLC 처리 확인 안 됨")
        if not s.valid:
            return self._result("미확인", code, t0, "상태 " + s.text())
        return self._result(("접수", "거부")[s.plc], code, t0)

    def _result(self, result: str, code: int, t0: float, reason: str = "") -> SendResult:
        r = SendResult(result, code, reason, self.status, time.monotonic() - t0)
        self._emit(result, "%d%s (%.2f초)" % (code, " " + reason if reason else "", r.elapsed))
        return r
