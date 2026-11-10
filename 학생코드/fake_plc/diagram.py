"""렁 그림(XG5000 LD 본문)을 읽어 PLC처럼 실행한다. 근거: docs/handoff/fake-plc.md 3.1~3.5.

해석기 DiagramPlc는 sim.Plc를 물려받는다. 접점·비교·동작 처리(_lit, _cmp, _do)와
스캔 규칙은 sim 것을 그대로 쓰고, 렁 그림에서 출력마다 조건을 구하는 부분만 새로 쓴다.

전위 흐름 (3.4)
- 칸 c(0..31)의 왼쪽 x = 3c+1. 전위를 칸 0부터 31까지 왼쪽에서 오른쪽으로 흘린다.
- 처음 전위는 1(왼쪽 레일)이다. 칸 0이 비어 있으면 0이 되므로 레일은 x=1 요소에만 들어간다.
- 접점·비교 상자는 켜져 있으면 통과, 아니면 0. 가로선은 그대로 통과. 빈 칸은 0.
- 칸 c를 모든 행에서 계산한 뒤, 그 오른쪽 경계(x=3c+3)의 세로선으로 이어진 행들을 OR한다.
- 출력(코일, SET, RST, 상자 동작, END)은 자기 칸에 들어온 전위를 조건으로 쓴다.
  렁의 전위를 모두 구한 다음 출력을 위에서 아래 순서로 실행한다.
"""

import os
import re
from dataclasses import dataclass, field

from tools.xgwx import codec, container, ladder_src, sim
from tools.xgwx.ladder_src import Action, Cmp, Lit

PASS = "-"                        # 전위를 그대로 넘기는 칸 (가로선, 비교 상자의 2·3번째 칸)
END = "END"
_SKIP = {(0xff, 0x3f), (0x01, 0x00), (0x01, 0x23), (0x02, 0x24)}  # 렁 설명, 세로선 참조, 상자 부분
_CONTACT = {0x06: {}, 0x07: {"neg": True}, 0x08: {"edge": True}}  # a, b, 상승 펄스(추정)
_COIL = {0x0e: "OUT", 0x10: "SET", 0x11: "RST"}                     # SET·RST는 추정
_BOX = {code: op for op, code in codec.BOX_CODE.items()
        if op not in ("CTU", "MOVP")}                               # 3.3: 텍스트 형식 미확인
# 상자 피연산자 자리마다 받는 것 (래더 원본 문법과 같다): 디바이스 첫 글자, '#'은 10진 정수
_OPERANDS = {"END": (), "TON": ("T", "#"), "MOV": ("D#", "D"), "BRST": ("PM", "#")}
_OPERANDS.update((op, ("DT", "D#")) for op in ladder_src.COMPARE_OPS)


@dataclass
class Rung:
    """해석기용 렁 하나."""
    rows: set = field(default_factory=set)        # 행 번호
    cells: dict = field(default_factory=dict)     # (행, 칸) → Lit, Cmp 또는 PASS
    vlines: dict = field(default_factory=dict)    # 칸 → 그 오른쪽 경계에서 세로선이 시작하는 위 행들
    outputs: list = field(default_factory=list)   # (행, 칸, Action 또는 END), 위에서 아래 순서
    cols: list = field(default_factory=list)      # 훑을 칸. 모든 행이 가로선이고 세로선도 없는 칸은 뺀다


def read_programs(path) -> list:
    """.xgwx·.bin → [(프로그램 이름, RungRec 목록)…]. .xgwx는 XML <Program> 순서다."""
    if str(path).lower().endswith(".bin"):          # 이미 풀린 ProgramData
        with open(path, "rb") as fh:
            return [(os.path.basename(path), codec.decode(fh.read()))]
    x = container.read(path)
    return [(name, codec.decode(container.get_program_data(x, name)))
            for name in container.program_names(x)]


def _cell(x):
    return (x - 1) // 3


def _operand(tok, kinds):
    """피연산자 하나를 검사한다. 모르는 형식이면 ValueError(tok). F 디바이스는 F0009B만 뜻을 안다."""
    if "#" in kinds and re.fullmatch("[0-9]+", tok):
        return tok
    if ladder_src.DEVICE_RE.match(tok) and tok[0] in kinds and (tok[0] != "F" or tok == sim.FIRST_SCAN):
        return tok
    raise ValueError(tok)


def name_of(e):
    """codec.name_of. text가 없는 kind 0x22(상자로 읽을 수 없는 저장 형식)는 UNKNOWN_0x22."""
    return "UNKNOWN_%#x" % e.kind if e.kind == 0x22 and "text" not in e.fields else codec.name_of(e)


def _line(n, e):
    """3.5의 미지원 한 줄."""
    return "미지원: 렁 %d, 요소 %s (kind=0x%02x, code=%s)" % (n, name_of(e), e.kind, e.fields.get("code", "-"))


def _box(rung, e):
    op = _BOX[e.fields["code"]]
    args = e.fields["text"].split(",")[1:]
    if len(args) != len(_OPERANDS[op]):
        raise ValueError(",".join(args))
    args = tuple(_operand(t, k) for t, k in zip(args, _OPERANDS[op]))
    c = _cell(e.x)
    if op in ladder_src.COMPARE_OPS:                 # 입력 쪽 조건, 세 칸
        rung.cells[e.y, c] = Cmp(op, *args)
        rung.cells[e.y, c + 1] = rung.cells[e.y, c + 2] = PASS
    else:
        rung.outputs.append((e.y, c, END if op == "END" else Action(op, args)))


def _compile(n, rec):
    """RungRec → (Rung, 미지원 줄 목록). n은 렁 번호(0부터)."""
    rung, bad = Rung(), []
    for e in rec.elements:
        tag = e.fields["tag"]
        try:
            if (tag, e.kind) == (0xff, 0x43):         # ROW 머리
                rung.rows.add(e.y)
            elif (tag, e.kind) in _SKIP:
                pass
            elif (tag, e.kind) == (0x00, 0x00):       # 세로선 본체: x=3c+3에서 y행과 y+1행
                rung.vlines.setdefault((e.x - 3) // 3, []).append(e.y)
                rung.rows.update((e.y, e.y + 1))
            elif (tag, e.kind) == (0xff, 0x02):       # 가로선: x부터 end_x 칸까지
                for c in range(_cell(e.x), _cell(e.fields["end_x"]) + 1):
                    rung.cells[e.y, c] = PASS
            elif tag == 0xff and e.kind in _CONTACT:
                dev = _operand(e.fields["device"], "PMTF")
                rung.cells[e.y, _cell(e.x)] = Lit(dev, **_CONTACT[e.kind])
            elif tag == 0xff and e.kind in _COIL:
                dev = _operand(e.fields["device"], "PM")
                rung.outputs.append((e.y, _cell(e.x), Action(_COIL[e.kind], (dev,))))
            elif (tag, e.kind) == (0x00, 0x22) and e.fields["code"] in _BOX:
                _box(rung, e)
            else:
                bad.append(_line(n, e))
        except ValueError as err:
            bad.append("%s, 피연산자 %s" % (_line(n, e), err))
    for tops in rung.vlines.values():
        tops.sort()
    rung.cols = [c for c in range(32)
                 if c in rung.vlines or any(rung.cells.get((y, c)) is not PASS for y in rung.rows)]
    return rung, bad


def unsupported(rungs) -> list:
    """프로그램 하나의 미지원 줄 목록 (3.5). 비어 있어야 실행할 수 있다."""
    return [line for n, rec in enumerate(rungs) for line in _compile(n, rec)[1]]


def _is_compare(e):
    return (e.fields["tag"], e.kind) == (0x00, 0x22) and _BOX.get(e.fields.get("code")) in ladder_src.COMPARE_OPS


def fidelity(rungs) -> list:
    """분기 뒤 접점 경고 (막지는 않는다). 출력이 둘 이상인 렁에서, x=1에 요소가 없어 세로선으로만
    전위를 받는 행에 접점이나 비교가 있으면 한 줄. 해석기는 렁의 전위를 다 구한 뒤 출력을 실행하지만(3.4),
    실물 XGB는 컴파일된 명령을 위에서 아래로 실행하므로 그 접점이 위 행 출력의 새 값을 같은 스캔에 볼 수 있다."""
    out = []
    for n, rec in enumerate(rungs):
        els = [e for e in rec.elements
               if (e.fields["tag"], e.kind) not in _SKIP and e.kind != 0x43]  # 행 머리, 참조, 상자 부분 빼고
        outputs = [e for e in els if e.kind in _COIL or (e.kind == 0x22 and not _is_compare(e))]
        rows = {}
        for e in els:
            rows.setdefault(e.y, []).append(e)
        if len(outputs) >= 2 and any(
                min(e.x for e in row) != 1 and any(e.kind in _CONTACT or _is_compare(e) for e in row)
                for row in rows.values()):
            out.append("렁 %d: 분기 뒤 접점 ― 실물(위→아래 실행)과 한 스캔 차이가 날 수 있음" % n)
    return out


class DiagramPlc(sim.Plc):
    """렁 그림 해석기. programs: 프로그램마다 RungRec 목록. 한 스캔에 차례로 모두 돈다 (3.7)."""

    def __init__(self, programs):
        bad = [line for rungs in programs for line in unsupported(rungs)]
        if bad:
            raise ValueError("\n".join(bad))          # 실행을 시작하지 않는다
        # self.rungs: 프로그램마다 해석기용 Rung 목록
        super().__init__([[_compile(n, rec)[0] for n, rec in enumerate(rungs)] for rungs in programs])

    def scan(self, inputs=None, dt_ms: int = 10) -> None:
        for dev, v in (inputs or {}).items():
            self.bits[dev] = int(v)
        self.bits[sim.FIRST_SCAN] = int(self.first)
        self.first = False
        for p, rungs in enumerate(self.rungs):
            for i, rung in enumerate(rungs):
                if not self._run((p, i), rung, dt_ms):
                    break                             # END: 이 프로그램은 여기서 끝

    def _run(self, key, rung, dt_ms) -> bool:
        """렁 하나를 실행한다. END를 만나면 False."""
        if not rung.cells and not rung.outputs:
            return True                               # 설명만 있는 렁
        came = self._flow(key, rung)
        for y, c, a in rung.outputs:
            if a is END:
                return False
            self._do(a, came[y, c], dt_ms)
        return True

    def _flow(self, key, rung) -> dict:
        """전위를 왼쪽에서 오른쪽으로 흘린다. 돌려주는 값: (행, 칸) → 그 칸에 들어온 전위.
        가로선뿐인 칸은 전위를 바꾸지 않으므로 건너뛴다(rung.cols)."""
        pot = dict.fromkeys(rung.rows, 1)             # 왼쪽 레일
        came = {}
        for c in rung.cols:
            for y in pot:
                came[y, c] = pot[y]
                cell = rung.cells.get((y, c))
                if isinstance(cell, Lit):             # 전위와 상관없이 평가한다 (에지 기억 갱신)
                    pot[y] &= self._lit(key + (y, c), cell)
                elif isinstance(cell, Cmp):
                    pot[y] &= self._cmp(cell)
                elif cell is not PASS:
                    pot[y] = 0                        # 빈 칸, 출력 칸
            tops = rung.vlines.get(c, ())             # 세로선: 이어진 행들을 OR
            for y in tops:                            # 위에서 아래로 모으고
                pot[y + 1] |= pot[y]
            for y in reversed(tops):                  # 아래에서 위로 나눠 준다
                pot[y] = pot[y + 1]
        return came
