"""래더 원본을 XG5000 템플릿의 프로그램 본문으로 넣어 .xgwx를 만든다.

템플릿은 사용자가 XG5000에서 만든 프로젝트다(프로그램 등록·표·꼬리 색인은 XG5000이 만든 그대로).
생성기는 이름이 맞는 <Program> 하나의 ProgramData만 바꾸고, 나머지 바이트는 템플릿 그대로 둔다.
실패하면 출력 파일을 만들지 않는다(임시 파일에 쓴 뒤 성공하면 이름을 바꾼다).

CLI: ``python -m tools.xgwx.build 원본.ld.txt 템플릿.xgwx 출력.xgwx [--program 이름]``
"""

import argparse
import os
import sys
import tempfile
from pathlib import Path

from tools.xgwx import codec, container, ladder_src
from tools.xgwx.layout import layout

DEFAULT_PROGRAM = "E2E_PICKNPLACE"


def _check_readback(raw: bytes, recs) -> None:
    """만든 본문을 다시 읽어 확인한다. 읽히지 않거나, 다시 쓴 바이트나 설명문이 다르면 ValueError."""
    back = codec.decode(raw)
    if codec.encode(back) != raw or [r.comment for r in back] != [r.comment for r in recs]:
        raise ValueError("만든 본문을 다시 읽은 결과가 다름 — 파일을 쓰지 않음")


def build(src_path, template_path, out_path, program: str = DEFAULT_PROGRAM) -> None:
    recs = layout(ladder_src.load(src_path).rungs)
    raw = codec.encode(recs)
    _check_readback(raw, recs)
    x = container.set_program_data(container.read(template_path), raw, program)
    out = Path(out_path)
    fd, tmp = tempfile.mkstemp(dir=out.parent, prefix=out.name, suffix=".tmp")
    os.close(fd)
    try:
        container.write(x, tmp)
        os.replace(tmp, out)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def program_devices(rungs) -> set:
    """풀어 놓은 프로그램(codec.RungRec 목록)이 쓰는 디바이스: 접점·코일의 device와
    상자(TON·MOV·비교 …) 피연산자 중 디바이스 형식인 것."""
    out = set()
    for r in rungs:
        for e in r.elements:
            if e.fields.get("device"):
                out.add(e.fields["device"])
            if e.kind == 0x22 and e.fields.get("text"):
                out.update(t for t in e.fields["text"].split(",")[1:]
                           if ladder_src.DEVICE_RE.match(t))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.xgwx.build",
                                 description="래더 원본을 XG5000 템플릿 프로그램에 넣는다")
    ap.add_argument("src", help="래더 원본 (.ld.txt)")
    ap.add_argument("template", help="XG5000에서 만든 템플릿 (.xgwx)")
    ap.add_argument("out", help="만들 파일 (.xgwx)")
    ap.add_argument("--program", default=DEFAULT_PROGRAM, help="본문을 바꿀 프로그램 이름")
    a = ap.parse_args(argv)
    build(a.src, a.template, a.out, a.program)
    print("만듦: %s (프로그램 %s)" % (a.out, a.program))
    return 0


if __name__ == "__main__":
    sys.exit(main())
