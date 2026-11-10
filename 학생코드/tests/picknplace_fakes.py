"""PICKNPLACE_FULL 시험용 도우미와 가짜 장치."""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Callable

from picknplace_full.status import TransportError


async def until(pred, timeout=1.0):
    """pred()가 참이 될 때까지 기다린다. 시간 안에 안 되면 실패."""
    end = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > end:
            raise AssertionError("%.2f초 안에 조건이 맞지 않음" % timeout)
        await asyncio.sleep(0.001)


@dataclass
class FakeTransport:
    """Transport 흉내. 레지스터는 raw(D01000)와 mailbox(D01001)다.

    - 우편함이 0이 아니면 process_after_reads번째 읽기에서 respond(코드)를 부른다.
      돌려준 값이 처리 뒤 raw이고 우편함은 0이 된다. None이면 처리하지 않는다(우편함 그대로).
    - 고장 주입: fail_reads는 앞으로 실패할 읽기 수, fail_write는 쓰기 실패.
    - 기록: writes는 성공한 쓰기, write_tries는 쓰기 시도 수, reads는 성공한 읽기 수.
    """

    raw: int = 2
    mailbox: int = 0
    respond: Callable[[int], int | None] = lambda code: None
    process_after_reads: int = 1
    fail_reads: int = 0
    fail_write: bool = False
    writes: list[int] = field(default_factory=list)
    write_tries: int = 0
    reads: int = 0
    name = "fake"
    _waited = 0  # 우편함이 찬 뒤 읽기 수

    async def connect(self) -> bool:
        return True

    async def read(self) -> tuple[int, int]:
        if self.fail_reads:
            self.fail_reads -= 1
            raise TransportError("응답 없음")
        self.reads += 1
        if self.mailbox:
            self._waited += 1
            if self._waited >= self.process_after_reads:
                raw = self.respond(self.mailbox)
                if raw is not None:
                    self.raw, self.mailbox = raw, 0
        return self.raw, self.mailbox

    async def write_command(self, code: int) -> None:
        self.write_tries += 1
        if self.fail_write:
            raise TransportError("쓰기 응답 없음")
        self.writes.append(code)
        self.mailbox, self._waited = code, 0

    async def close(self) -> None:
        pass

    def lost(self) -> bool:
        return False
