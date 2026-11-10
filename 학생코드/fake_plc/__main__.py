"""python -m fake_plc [FILE] [--modbus-port 5020] [--wiring-port 5021] [--http-port 8878]

가짜 PLC를 띄운다. 사용법은 fake_plc/README.md에 있다.
"""

import argparse
import asyncio
import sys
from pathlib import Path

from fake_plc.app import FakePlc

# 노트에 기본 파일이 없다. 참조 래더를 쓴다.
DEFAULT_FILE = Path(__file__).resolve().parents[1] / "ladder" / "PICKNPLACE_FULL.xgwx"


def arguments(argv=None):
    p = argparse.ArgumentParser(prog="python -m fake_plc",
                                description="가짜 PLC: 래더를 스캔하고 Modbus TCP, 배선 JSON, 모니터 UI를 연다")
    p.add_argument("file", nargs="?", default=str(DEFAULT_FILE),
                   help="래더 파일 (.xgwx, .ld.txt, .bin). 기본: ladder/PICKNPLACE_FULL.xgwx")
    p.add_argument("--modbus-port", type=int, default=5020, help="Modbus TCP (기본 5020)")
    p.add_argument("--wiring-port", type=int, default=5021, help="배선 JSON (기본 5021)")
    p.add_argument("--http-port", type=int, default=8878, help="모니터 UI (기본 8878)")
    args = p.parse_args(argv)
    if not all(0 <= port <= 65535 for port in (args.modbus_port, args.wiring_port, args.http_port)):
        p.error("포트는 0~65535입니다")
    return args


async def serve(args):
    app = FakePlc(args.file)
    await app.start(args.modbus_port, args.wiring_port, args.http_port)
    e = app.engine
    print("가짜 PLC 모니터  http://127.0.0.1:%d" % app.ports["http"])
    print("Modbus TCP      127.0.0.1:%d (국번 1, 주소 n = D(1000+n))" % app.ports["modbus"])
    print("배선 JSON       127.0.0.1:%d (가짜 로봇이 붙는다)" % app.ports["wiring"])
    print("파일            %s · %s" % (e.path, "RUN" if e.running else "STOP"))
    for line in e.problems + e.warnings:
        print("  " + line)
    sys.stdout.flush()
    try:
        await asyncio.Event().wait()                    # Ctrl+C까지
    finally:
        await app.close()


def main(argv=None):
    sys.stdout.reconfigure(errors="replace")            # 콘솔(cp949)에 없는 글자가 있어도 멈추지 않는다
    try:
        asyncio.run(serve(arguments(argv)))
    except KeyboardInterrupt:
        print("가짜 PLC를 끝냈다.")
    except OSError as error:                            # 포트가 이미 쓰이는 중 등
        raise SystemExit("가짜 PLC를 띄울 수 없음: %s" % error)


if __name__ == "__main__":
    main()
