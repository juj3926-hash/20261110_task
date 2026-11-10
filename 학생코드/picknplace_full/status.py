"""PICKNPLACE_FULL 상태 코드 해석 (spec 2.3절)."""

from dataclasses import dataclass

START, MOVE1, MOVE2, ESTOP = 1111, 9991, 9992, 9995
RESUME, AUTO_OFF, GRIPPER, CHECK = 9993, 9994, 3333, 2222

ROBOT_TEXT = ("동작 중", "정지", "비정상 정지")
GRIPPER_TEXT = ("열림", "닫힘")
PLC_TEXT = ("정상", "이상")


class TransportError(Exception):
    """통신 실패. 메시지는 한국어 사유다."""


@dataclass(frozen=True)
class Status:
    raw: int
    mailbox: int
    at: float
    robot: int | None
    gripper: int | None
    plc: int | None

    @property
    def valid(self) -> bool:
        return self.robot is not None

    def text(self) -> str:
        if not self.valid:
            return "알 수 없는 코드 %d" % self.raw
        return " · ".join((ROBOT_TEXT[self.robot], GRIPPER_TEXT[self.gripper], PLC_TEXT[self.plc]))


def decode(raw: int, mailbox: int, at: float) -> Status:
    if not 1 <= raw <= 12:
        return Status(raw, mailbox, at, None, None, None)
    n = raw - 1
    return Status(raw, mailbox, at, n % 3, n // 3 % 2, n // 6)
