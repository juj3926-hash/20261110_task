"""python -m picknplace_full [--target auto|serial:COMn|tcp:HOST:PORT|model] [--http-port 8877] [--interval 0.2] [--speed 1]"""

import asyncio

from picknplace_full.app import arguments, serve

try:
    asyncio.run(serve(arguments()))
except KeyboardInterrupt:
    print("PC 프로그램을 끝냈습니다. 끝내기는 정지 명령이 아닙니다. 로봇과 PLC 상태는 따로 확인하세요.")
except (OSError, ValueError) as e:  # 화면 포트를 이미 쓰는 중이거나 대상이 틀렸다
    raise SystemExit(str(e))
