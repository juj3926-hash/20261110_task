"""교육 모형만 여는 실행 입구. 실제 COM 포트를 찾거나 연결하지 않는다."""
import argparse
import asyncio
import math
import threading
from picknplace_full.app import serve


async def fault_ui(args):
    """고장은 모형에서 첫 9991 응답에만 한 번 주입한다. HTTP 기능은 제공 앱 그대로다."""
    from picknplace_full.app import EventLog, LOGS, make_server
    from picknplace_full.link import PlcLink
    from picknplace_full.model import VirtualPlant
    from picknplace_full.sequence import Sequencer
    from lesson_scenarios import LessonTransport
    link = PlcLink(LessonTransport(VirtualPlant(speed=args.speed), args.fault), interval=args.interval)
    log = EventLog()
    link.on_event(log.write)
    seq = Sequencer(link, log=log.log)
    server = make_server(link, seq, asyncio.get_running_loop(), args.http_port, log.events)
    with server, log.open(LOGS):
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            await link.start()
            log.log("교육 모형", "첫 이동1 쓰기 응답을 한 번 유실합니다. 실제 전달은 일어납니다.")
            print("http://127.0.0.1:%d · target=model · fault=%s" % (server.server_port, args.fault), flush=True)
            await asyncio.Event().wait()
        finally:
            await seq.close()
            await link.close()
            await asyncio.to_thread(server.shutdown)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--http-port", type=int, default=8877)
    p.add_argument("--interval", type=float, default=0.2)
    p.add_argument("--speed", type=float, default=1.0)
    p.add_argument("--fault", choices=("write_reply_lost",), help="첫 이동1 응답을 한 번 유실하는 모형")
    args = p.parse_args()
    if (not 0 <= args.http_port <= 65535 or not math.isfinite(args.interval)
            or not math.isfinite(args.speed) or args.interval <= 0 or args.speed <= 0):
        p.error("포트는 0~65535, interval과 speed는 양수여야 합니다")
    args.target = "model"
    print("교육 모형 UI: 실물 연결·물리 성공 확인이 아닙니다.", flush=True)
    try:
        asyncio.run(fault_ui(args) if args.fault else serve(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
