"""실제 통신 부품과 래더 모형으로 고장을 재현한다. 실제 장치에 연결하는 옵션은 없다."""
import argparse
import asyncio
import json
import time

from picknplace_full.link import PlcLink
from picknplace_full.model import ModelTransport, VirtualPlant
from picknplace_full.sequence import Sequencer
from picknplace_full.status import CHECK, MOVE1, TransportError

CASES = ("observe", "normal", "stop", "pause", "delay", "read_fail", "read_timeout", "write_reply_lost", "final_open_estop")


class LessonTransport:
    """모형만 감싼다. 지연 중에도 VirtualPlant의 별도 asyncio 작업은 진행한다."""
    name = "model · 수업 고장 재현"

    def __init__(self, plant, fault=None):
        self.inner = ModelTransport(plant)
        self.plant = plant
        self.fault = fault
        self.used = False
        self.delay = 0.0
        self.fail_until = 0.0
        self.writes = []

    async def connect(self):
        return await self.inner.connect()

    async def read(self):
        await asyncio.sleep(self.delay)
        if time.monotonic() < self.fail_until:
            raise TransportError("교육 모형: 읽기 응답 유실")
        return await self.inner.read()

    async def write_command(self, code):
        await asyncio.sleep(self.delay)
        self.writes.append(code)
        await self.inner.write_command(code)
        if self.fault == "write_reply_lost" and code == MOVE1 and not self.used:
            self.used = True
            raise TransportError("교육 모형: 실제 전달 뒤 쓰기 응답만 유실")

    async def close(self):
        await self.inner.close()

    def lost(self):
        return False


async def until(pred, timeout=15):
    deadline = time.monotonic() + timeout
    while not pred():
        if time.monotonic() >= deadline:
            raise AssertionError("모형 시연 준비/결과를 제한 시간 안에 확인하지 못했습니다")
        await asyncio.sleep(0.01)


async def ready(link, plant):
    await until(lambda: link.comm_ok and link.status.valid and not plant.bit("G_OPENING")
                and not plant.bit("G_CLOSING"))


def snapshot(link):
    s = link.status
    return {"comm_ok": link.comm_ok, "raw": s.raw, "mailbox": s.mailbox,
            "robot": s.robot, "gripper": s.gripper, "plc": s.plc, "observed_at": s.at}


async def run(case, speed=1.0):
    plant = VirtualPlant(speed=speed)
    transport = LessonTransport(plant, "write_reply_lost" if case == "write_reply_lost" else None)
    link = PlcLink(transport, interval=0.05, fast_interval=0.02, timeout=2)
    started = time.monotonic()
    def log(tag, text):
        print("%6.2f [%s] %s" % (time.monotonic() - started, tag, text), flush=True)
    link.on_event(lambda event: log(event.tag, event.text))
    seq = Sequencer(link, move_timeout=15, grip_timeout=8, log=log)
    print("교육 모형 전용 / 실물 연결 없음 / 고정 커밋의 래더 모형", flush=True)
    summary = {"mode": "model", "case": case}
    await link.start()
    try:
        await ready(link, plant)
        if case == "observe":
            summary["before"] = snapshot(link)
            answer = await link.send(CHECK)
            summary["command"] = {"code": CHECK, "result": answer.result}
            summary["after"] = snapshot(link)
        elif case in ("normal", "stop", "read_timeout", "final_open_estop"):
            if case == "read_timeout":
                seq.move_timeout = 0.2
                def break_reads(event):
                    if event.tag == "접수" and event.text.startswith("9991 "):
                        transport.fail_until = time.monotonic() + 0.5
                link.on_event(break_reads)
            task = asyncio.create_task(seq.auto_cycle())
            if case == "final_open_estop":
                await until(lambda: seq.step.startswith("5단계") or task.done())
                if not task.done():
                    summary["stop_request"] = await seq.press("estop")
            if case == "stop":
                await until(lambda: plant.robot.state == "동작" or task.done())
                if not task.done():
                    summary["stop_request"] = await seq.press("stop")
            summary["result"] = await task
            expected = "정지" if case == "stop" else "완료"
            if case == "read_timeout":
                if "시간 초과" not in summary["result"] or transport.writes != [1111, 9991]:
                    raise AssertionError("시간 초과 중단 뒤 다음 이동2를 보내면 안 됩니다")
                await until(lambda: link.comm_ok)
                summary["auto_maybe_on"] = seq.auto_maybe_on
                summary["result_confirmed"] = False
            elif summary["result"] != expected:
                raise AssertionError("A/B/C 구현 필요: 기대 %s, 실제 %s" % (expected, summary["result"]))
            if case == "final_open_estop":
                summary["resume_sent"] = 9993 in transport.writes
                summary["robot_abnormal_remains"] = link.status.robot == 2
                summary["task_complete_is_not_stop_released"] = True
                if summary["resume_sent"] or not summary["robot_abnormal_remains"]:
                    raise AssertionError("최종 열림 완료와 즉시 정지 해제를 구분해야 합니다")
        elif case == "pause":
            summary["move"] = await seq.press("script1")
            await until(lambda: plant.robot.state == "동작")
            summary["stop"] = await seq.press("estop")
            await until(lambda: link.status.robot == 2 and plant.bit("DO1"))
            remaining = plant.robot.left
            await asyncio.sleep(0.15)
            summary["model_motion_paused"] = plant.robot.left == remaining
            summary["resume"] = await seq.press("resume")
            s = await link.wait(lambda s: s.robot == 1, 15)
            if s is None or not summary["model_motion_paused"]:
                raise AssertionError("모형 정지/재개 조건 불일치")
            summary["result"] = "모형 정지·재개 확인"
        elif case == "delay":
            transport.delay = 0.2
            ticks = 0
            async def heartbeat():
                nonlocal ticks
                while True:
                    ticks += 1
                    await asyncio.sleep(0.03)
            beat = asyncio.create_task(heartbeat())
            try:
                answer = await link.send(CHECK)
            finally:
                beat.cancel()
                await asyncio.gather(beat, return_exceptions=True)
            summary.update(command_result=answer.result, heartbeat_ticks=ticks, elapsed=round(answer.elapsed, 3))
            if answer.result != "접수" or ticks < 3:
                raise AssertionError("비동기 대기 중 다른 작업이 진행되지 않았습니다")
        elif case == "read_fail":
            summary["move"] = await seq.press("script1")
            before = snapshot(link)
            transport.fail_until = time.monotonic() + 0.4
            await until(lambda: not link.comm_ok)
            await asyncio.sleep(0.1)
            summary["during"] = snapshot(link)
            summary["last_observation_preserved"] = link.status.at == before["observed_at"]
            await until(lambda: link.comm_ok)
            summary["after_recovery"] = snapshot(link)
            if not summary["last_observation_preserved"] or summary["during"]["comm_ok"]:
                raise AssertionError("읽기 실패가 오래된 관측을 새 관측으로 바꿨습니다")
            summary["result"] = "통신 회복 확인 / 작업 완료는 별도 판정"
        elif case == "write_reply_lost":
            answer = await link.send(MOVE1)
            summary["command_result"] = answer.result
            await until(lambda: plant.robot.state == "동작")
            summary["model_motion_started"] = True
            await link.wait(lambda s: s.robot == 1, 15)
            summary["write_count"] = len(transport.writes)
            if answer.result != "미확인" or transport.writes != [MOVE1]:
                raise AssertionError("쓰기 응답 유실 뒤 자동 재전송하면 안 됩니다")
        summary["writes"] = transport.writes
        summary["final_observation"] = snapshot(link)
        print("SUMMARY " + json.dumps(summary, ensure_ascii=False), flush=True)
        return summary
    finally:
        await seq.close()
        await link.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=CASES, required=True)
    parser.add_argument("--speed", type=float, default=1.0, help="모형 배속, 기본 1, 허용 0.5~10")
    args = parser.parse_args()
    if not 0.5 <= args.speed <= 10:
        parser.error("speed는 0.5~10이어야 합니다")
    asyncio.run(run(args.case, args.speed))
