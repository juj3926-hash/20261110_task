"""python -m fake_robot [--plc-host 127.0.0.1] [--wiring-port 5021] [--http-port 8879]"""

import asyncio

from fake_robot.server import arguments, serve

try:
    asyncio.run(serve(arguments()))
except KeyboardInterrupt:
    print("가짜 로봇 종료")
except OSError as error:
    raise SystemExit(f"시작 실패: {error}")
