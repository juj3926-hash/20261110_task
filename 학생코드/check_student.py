"""학생의 실제 함수를 호출하는 단계별 동작 검사. 실물에 연결하지 않는다."""
import argparse
import asyncio
import json
import time
from types import SimpleNamespace

from picknplace_full.link import PlcLink, SendResult
from picknplace_full.sequence import Abort, Sequencer
from picknplace_full.status import AUTO_OFF, MOVE1, MOVE2, START, decode as decode_status
from tests.picknplace_fakes import FakeTransport, until


def need(condition, message):
    if not condition:
        raise AssertionError(message)


def decode(raw, mailbox):
    return decode_status(raw, mailbox, time.time())


async def accept_case(result, command=MOVE1):
    sent = []
    events = []
    async def send(code):
        events.append("send")
        sent.append(code)
        return SendResult(result, code, "검사 응답", decode(2, 0), 0.01)
    seq = Sequencer(SimpleNamespace(send=send))
    async def stop_wait(pred, timeout):
        events.append("wait")
        need(all(pred(decode(raw, 0)) for raw in (2, 5))
             and all(not pred(decode(raw, 0)) for raw in (1, 3, 4, 6)),
             "전송 전 robot == 1인 새 정지 관측을 기다려야 합니다")
        need(timeout == seq.move_timeout, "전송 전 정지 대기는 move_timeout을 사용합니다")
    seq._until = stop_wait  # C의 미완성 여부와 분리하여 A의 호출 순서를 검사한다.
    seq.step = "이동1"
    error = None
    try:
        await seq._accept(command)
    except Abort as e:
        error = str(e)
    need(sent == [command], "전달받은 코드를 정확히 한 번 보내야 합니다")
    need(events == (["send"] if command == START else ["wait", "send"]),
         "START만 선행 대기를 생략하며 나머지는 정지 대기 뒤 전송해야 합니다")
    need((error is None) == (result == "접수"), "접수는 계속하고 거부·미확인은 Abort로 중단해야 합니다")
    if error is not None:
        need(result in error and seq.step in error, "중단 이유에 단계와 접수 판정을 남겨야 합니다")


async def accept_wait_timeout():
    sent = []
    waiting = asyncio.Event()
    release = asyncio.Event()
    async def send(code):
        sent.append(code)
        return SendResult("접수", code, "", decode(2, 0), 0.01)
    async def wait(pred, timeout):
        waiting.set()
        await release.wait()
        return None  # 새 정지 관측을 얻지 못한 경우
    seq = Sequencer(SimpleNamespace(send=send, wait=wait))
    async def until_probe(pred, timeout):
        if await seq.link.wait(pred, timeout) is None:
            raise Abort("시험: 전송 전 정지 대기 시간 초과")
    seq._until = until_probe
    task = asyncio.create_task(seq._accept(MOVE1))
    try:
        await asyncio.sleep(0)
        need(waiting.is_set() and not sent and not task.done(), "정지를 기다리는 동안 명령을 보내면 안 됩니다")
        release.set()
        try:
            await task
        except Abort:
            pass
        else:
            raise AssertionError("전송 전 정지 대기 실패는 중단으로 이어져야 합니다")
        need(not sent, "정지 대기 시간 초과 뒤 명령을 보내면 안 됩니다")
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def cycle_case(stop_at=None):
    # A와 C를 시험 대역으로 바꾸고 학생의 _cycle만 실행한다.
    seq = Sequencer(SimpleNamespace())
    events = []
    waits = 0
    async def accept(code):
        events.append(("send", code))
        if stop_at == "after_start" and code == START:
            seq.stop_requested = True
        if stop_at == "after_move1" and code == MOVE1:
            seq.stop_requested = True
        if stop_at == "after_move2" and code == MOVE2:
            seq.stop_requested = True
    async def wait(pred, timeout):
        nonlocal waits
        modes = ["robot", "closed", "robot", "open"]
        need(waits < 4, "정상 사이클의 완료 대기는 네 번입니다")
        mode = modes[waits]
        # 상태 1: 이동/열림, 2: 정지/열림, 5: 정지/닫힘, 4: 이동/닫힘.
        yes, no = {"robot": ((2, 5), (1, 3, 4, 6)),
                   "closed": ((5,), (2,)),
                   "open": ((2,), (5,))}[mode]
        need(all(bool(pred(decode(raw, 0))) for raw in yes)
             and all(not bool(pred(decode(raw, 0))) for raw in no),
             "완료 대기 조건이 현재 단계와 맞지 않습니다: " + mode)
        need(timeout == (seq.move_timeout if mode == "robot" else seq.grip_timeout),
             "이동/그리퍼의 제한 시간을 구분해야 합니다")
        events.append(("wait", mode))
        waits += 1
    seq._accept, seq._until = accept, wait
    result = await seq._cycle()
    base = [("send", START), ("send", MOVE1), ("wait", "robot"), ("wait", "closed"),
            ("send", MOVE2), ("wait", "robot"), ("wait", "open")]
    expected = base
    if stop_at == "after_start":
        expected = [("send", START), ("send", AUTO_OFF)]
    elif stop_at == "after_move1":
        expected = base[:4] + [("send", AUTO_OFF)]
    need(events == expected, "요청·완료 대기·정지 경계가 맞지 않습니다. 실제: " + repr(events))
    need(result == ("정지" if stop_at in ("after_start", "after_move1") else "완료"),
         "사이클의 반환 결과를 완료/정지로 구분해야 합니다")


async def wait_case(kind):
    transport = FakeTransport()
    link = PlcLink(transport, interval=0.01, fast_interval=0.005)
    await link.start()
    try:
        await until(lambda: link.comm_ok)
        seq = Sequencer(link)
        seq.step = "이동1"
        if kind == "fresh":
            transport.fail_reads = 1000
            await until(lambda: not link.comm_ok)
            old_at = link.status.at
            task = asyncio.create_task(seq._until(lambda s: s.robot == 1, 0.3))
            try:
                await asyncio.sleep(0.04)
                need(not task.done(), "오래된 정지 상태를 새 완료로 쓰면 안 됩니다")
                transport.fail_reads = 0
                await task
                need(link.status.at > old_at, "호출 이후 새 읽기가 필요합니다")
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        else:
            transport.fail_reads = 1000
            await until(lambda: not link.comm_ok)
            followup = []
            try:
                await seq._until(lambda s: s.robot == 1, 0.05)
                followup.append(MOVE2)
            except Abort as e:
                need("시간 초과" in str(e) and seq.step in str(e), "시간 초과 이유에 단계와 제한 시간을 남기세요")
            else:
                raise AssertionError("새 관측이 없는 시간 초과는 Abort로 중단해야 합니다")
            need(not followup, "시간 초과 뒤 다음 명령을 보내면 안 됩니다")
    finally:
        await link.close()


async def policy_case():
    transport = FakeTransport(respond=lambda code: 2)
    link = PlcLink(transport, interval=0.005, fast_interval=0.005)
    await link.start()
    try:
        await until(lambda: link.comm_ok)
        seq = Sequencer(link)
        names = ("script1", "script2", "gripper")
        need(all(seq.buttons()[n]["enabled"] for n in names), "정상 준비 상태의 개별 버튼은 허용합니다")
        seq.auto_maybe_on = True
        need(all(not seq.buttons()[n]["enabled"] for n in names), "복구 미확인 상태의 개별 세 버튼을 보류해야 합니다")
        for name in names:
            await seq.press(name)
        need(transport.writes == [], "화면뿐 아니라 실제 press 경로에서도 보류해야 합니다")
        need(seq.buttons()["stop"]["enabled"] and seq.buttons()["estop"]["enabled"],
             "복구 해제와 즉시 정지 경로는 남겨야 합니다")
        await seq.press("stop")
        need(transport.writes == [AUTO_OFF] and not seq.auto_maybe_on, "9994 접수로 복구 추정을 해제해야 합니다")
        need(all(seq.buttons()[n]["enabled"] for n in names), "해제 접수 후 정상 조건에서 개별 버튼을 다시 허용합니다")
    finally:
        await link.close()


async def run(args):
    cases = []
    if args.stage in ("A", "all"):
        cases += [("A " + r, lambda r=r: accept_case(r)) for r in ("접수", "거부", "미확인")]
        cases += [("A START는 선행 대기 생략", lambda: accept_case("접수", START)),
                  ("A 전송 전 정지 대기 시간 초과", accept_wait_timeout)]
    if args.stage in ("B", "all"):
        if args.part in ("normal", "all"):
            cases.append(("B 정상 순서", lambda: cycle_case()))
        if args.part in ("stop", "all"):
            cases += [("B 정지 " + s, lambda s=s: cycle_case(s)) for s in ("after_start", "after_move1", "after_move2")]
    if args.stage in ("C", "all"):
        cases += [("C " + s, lambda s=s: wait_case(s)) for s in ("fresh", "timeout")]
    if args.stage in ("D", "all"):
        cases.append(("D 복구 전 보류/해제 후 허용", policy_case))
    passed = 0
    for name, case in cases:
        try:
            await asyncio.wait_for(case(), timeout=3)
        except Exception as e:
            print("FAIL", name, type(e).__name__, str(e), flush=True)
        else:
            passed += 1
            print("PASS", name, flush=True)
    print(json.dumps({"mode": "model-test", "stage": args.stage, "part": args.part,
                      "passed": passed, "total": len(cases)}, ensure_ascii=False))
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("A", "B", "C", "D", "all"), default="all")
    parser.add_argument("--part", choices=("normal", "stop", "all"), default="all",
                        help="B만 정상/정지로 나누며 기본은 모두 검사")
    raise SystemExit(asyncio.run(run(parser.parse_args())))
