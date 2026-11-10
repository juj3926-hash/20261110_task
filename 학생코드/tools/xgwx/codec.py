"""출처: method.system_programming/task1_ladder/tools/xgwx_codec.py (2026-11-03)

XG5000 LD 프로그램 바이너리(ProgramData를 base64+bzip2로 푼 것) 코덱.
이 저장소에서 바꾼 것: import 경로, make_box 추가.

C:\\XG5000\\Projects 아래 xgwx와 백업(xgwx_bkx*)에서 ProgramData 블록 46개,
서로 다른 내용 23종(8바이트 ~ 13.7KB)을 모아 해독했다. 23종 모두
``encode(decode(raw)) == raw`` 가 성립한다(tests/samples/programdata).

정수는 모두 little-endian. 문자열(CSTR)은 MFC CString 유니코드 형식:
``ff fe ff <글자수 u8>`` + UTF-16LE (표본 문자열은 모두 255자 미만).

좌표 POS(u32) = ``x | (row << 10)``. 즉 바이트0 = x, 바이트1 = row*4
(하위 2비트는 표본 2829개에서 항상 0). x 단위는 칸의 1/3:
칸 c(0..31)의 왼쪽 = ``x = 3c+1``. 출력 칸(코일/END)은 c=31 → x=94(0x5e),
3칸짜리 상자(TON, MOV …)는 c=29 → x=88(0x58)부터, 가로선은 끝 칸의 x까지
(출력 바로 앞이면 91(0x5b), 상자 앞이면 85(0x55)). 세로선은 칸 c의 오른쪽 경계
``x = 3c+3`` (c=0이면 x=3).

프로그램 구조::

    PROGRAM = u32 0 | u16 총행수 | u16 렁수 | RUNG*렁수
              총행수 = 마지막 행 번호 + 1 (빈 행은 저장하지 않음)
              빈 프로그램 = 8바이트 0
    RUNG    = u32 렁번호(0부터 연속) | u32 _raw_flag(0/1, 뜻 미확인)
              | u16 행수 | ROW*행수
    ROW     = u32 행번호(절대, 렁 안에서 연속) | ELEM(ROW 머리, kind 0x43)
              | ELEM*n  (n은 ROW 머리 마지막 u16)
    ELEM    = u8 tag | u32 kind | (kind/tag별 본문)

tag: 0xff = 보통 요소, 0x00 = 여러 부분으로 된 요소의 첫 부분(세로선, 상자),
0x01/0x02 = 그 뒤 부분(세로선 참조, 상자 2·3번째 칸).

요소 표 (n은 표본에서 본 개수):

| tag | kind | 뜻 | 본문 (tag·kind 뒤) |
|-----|------|----|--------------------|
| ff | 0x43 | ROW 머리 (n=857) | u32 _raw_pos(=0) · u32 comment_row(설명 행이면 1) · u32 _raw_a(39 대부분, 47/36/44/50/58/59) · u32 right(POS: 이 행의 출력 x, 없으면 94) · u32 _raw_c(POS, x=94, 행은 이 행 이하) · u32 _raw_d(POS, 커서로 보임; 대개 (1,행)) · u16 n(요소 수) |
| ff | 0x02 | HLINE 가로선 (729) | POS · u16 a=0 · u32 flags=0 · u32 끝 POS(end_x, 같은 행) |
| ff | 0x06 | CONTACT_NO a접점 (648) | POS · u16 a=1 · u32 flags=0 · CSTR device |
| ff | 0x07 | CONTACT_NC b접점 (76) | 위와 같음 |
| ff | 0x08 | CONTACT_P 상승 펄스 접점 (2, 추정*) | 위와 같음 |
| ff | 0x0e | COIL 출력 코일 (157) | POS · u16 a=1 · u32 flags=0x20 · CSTR device |
| ff | 0x10 | SET 코일 (135, 추정*) | 위와 같음 |
| ff | 0x11 | RST 코일 (102, 추정*) | 위와 같음 |
| ff | 0x3f | RUNG_COMMENT 렁 설명 (181) | POS(1,행) · u16 a=0 · u32 flags=0x20 · CSTR text · 8바이트 _raw_tail(대개 `0d000000 0d000000`, 한 번은 쓰레기 값) |
| 00 | 0x00 | VLINE 세로선 본체 (260) | (POS 없음) u16 링크수(=2) · 링크*2, 링크 = POS · u16 0 · u32 0. 링크는 항상 [(x,행), (x-1,행+1)] |
| 01 | 0x00 | VLINE 참조 (260) | POS = 위 행 세로선의 (x, 행). 세로선 아래 행에 들어감 |
| 00 | 0x22 | BOX 명령 상자 (185: TON, MOV, END, CTU, 비교 …) | POS · u16 a=1 · u32 flags(0x20, 비교 상자는 0) · u32 code · CSTR text("TON,T0000,100") · u16 피연산자수 · OP*수, OP = POS · u16 a(첫째 1, 나머지 0) · u32 flags · CSTR |
| 01 | 0x23 | BOX_PART1 상자 2번째 부분 (177) | POS(상자와 같음) |
| 02 | 0x24 | BOX_PART2 상자 3번째 부분 (177) | POS(상자와 같음) |

\\* 0x08/0x10/0x11은 XG5000 화면 대조를 못 했다(XG5000 실행에 UAC 승격이
필요). **추정이며 Task 6 스파이크에서 XG5000 화면으로 확인 예정.** 근거: 공정 단계 렁에서 ``M(n) · 조건 → [0x10] M(n+1), [0x11] M(n)``
꼴로 쓰임(SET 다음 단계 / RST 현재 단계), 0x0e는 P0004x 출력에만 쓰임,
0x08은 ``M00300 · [0x08]P00000 → [0x10]M00301`` (시작 버튼 상승 에지).
XG5000 도구 모음 순서(a, b, P, N 접점 / 코일, 반전, SET, RST …)와도 맞는다.

상자 code: END 0, TON 913, CTU 925, MOV 148, MOVP 404, BRST 357,
비교 = 1254, > 1256, < 1258, >= 1260, <= 1262. TON/MOV 등 3칸 상자는 x=88,
피연산자 x는 88/91/94, 뒤에 BOX_PART1·BOX_PART2(같은 POS)가 따라온다.
END는 x=94, 피연산자 1개("END"), 부분 요소 없음, code 0.

관찰된 규칙(표본 전부 성립): 행 안 요소는 x 오름차순(세로선 참조는 같은 x의
본체보다 앞), 렁 설명은 혼자 한 렁(행 1개), 마지막 렁은 END 렁(가로선 1→91 +
END 상자). flags에 0x04가 더해진 요소가 가끔 있음(뜻 미확인, 그대로 보존).

최소 입력에서 정할 수 없는 값(편집기 상태로 보임, 표본 598렁 비교로 확인;
나머지 바이트는 전부 최소 입력에서 재현된다 — tests TestFidelityFromMinimal):
ROW 머리 _raw_a(39 대부분; 편집 시기별 47/36/44/50/58/59), _raw_c(옛 행 번호),
_raw_d(커서), 렁 _raw_flag, 요소 flags의 0x04 비트, 설명 _raw_tail 쓰레기 값.
encode 기본값은 39 / (94,행) / (1,행) / 0 / 0x04 없음 / 0d000000 0d000000.
비교 상자(=,<,>,>=,<=)는 입력 쪽이라 상자·피연산자 flags가 0, 나머지는 0x20.

이름으로 요소 만들기 (KIND는 저장 코드라 TON=END=BOX=0x22 로 되짚을 수 없다.
Task 6은 이름을 KIND에서 거꾸로 찾지 말고 아래를 쓴다):
- ``make_contact(dev, x, y, neg=False, edge=False)`` → 0x06 / 0x07 / 0x08
- ``make_coil(dev, y, x=94)``, ``make_set(dev, y, x=94)``, ``make_rst(dev, y, x=94)``
- ``make_ton(dev, preset, y, x=88)`` → "TON,dev,preset", code 913
- ``make_end(y, x=94)`` (렁 = ``make_hline(1, y, 91)`` + ``make_end(y)``)
- ``make_hline(x, y, end_x)``
- ``make_vline(x, y)`` → [본체(y행), 참조(y+1행)] 두 요소. 렁 목록에 ``*`` 로 펼쳐 넣는다
- ``make_comment(text, y)`` → 혼자 한 렁
- ``name_of(e)`` → "CONTACT_NO", "SET", "TON", "END", "BOX:MOV", "VLINE_REF" …
ROW 머리 없이 넘긴 렁은 encode가 행마다 x 오름차순(같은 x면 세로선 참조 먼저)으로
정렬하고 상자 부분(BOX_PART1/2)을 붙인다.

파이썬 표현:
- ``decode`` 결과의 렁마다 ``elements``는 저장 순서 그대로이며 각 행 앞에
  ROW 머리 요소(kind 0x43, x=0, y=행번호)가 끼어 있다. 모든 원본 값이
  ``fields``에 들어가므로 바이트 그대로 다시 쓸 수 있다.
- ``Element.x/y``: POS의 x와 행. VLINE 본체는 첫 링크, VLINE 참조는 위 행.
- ``encode``는 개수(총행수, 렁수, 행수, 요소 수)를 다시 센다. 렁에 ROW 머리가
  하나도 없으면 행을 스스로 나누고(요소의 y, VLINE 참조는 y+1), ROW 머리 기본값,
  플래그/a 기본값, VLINE 링크, 상자 피연산자와 BOX_PART1/2를 채운다 →
  Task 6은 kind·x·y와 device/text/end_x만 주면 된다.
"""

import base64
import bz2
import dataclasses
import struct
from typing import Iterator, List

from tools.xgwx import container as xgwx_container

KIND = {
    "VLINE": 0x00,
    "HLINE": 0x02,
    "CONTACT_NO": 0x06,
    "CONTACT_NC": 0x07,
    "CONTACT_P": 0x08,
    "COIL": 0x0e,
    "SET": 0x10,
    "RST": 0x11,
    "TON": 0x22,
    "END": 0x22,
    "BOX": 0x22,
    "BOX_PART1": 0x23,
    "BOX_PART2": 0x24,
    "RUNG_COMMENT": 0x3f,
    "ROW": 0x43,
}

BOX_CODE = {"END": 0, "TON": 913, "CTU": 925, "MOV": 148, "MOVP": 404,
            "BRST": 357, "=": 1254, ">": 1256, "<": 1258, ">=": 1260,
            "<=": 1262}

_COMPARE = {"=", ">", "<", ">=", "<="}
_OUTPUTS = {0x0e, 0x0f, 0x10, 0x11, 0x12, 0x13}
_DEFAULT_COMMENT_TAIL = b"\x0d\0\0\0\x0d\0\0\0"
OUT_X = 94
BOX_X = 88


@dataclasses.dataclass
class Element:
    kind: int
    x: int
    y: int
    fields: dict = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class RungRec:
    index: int
    elements: List[Element]
    comment: str = ""
    fields: dict = dataclasses.field(default_factory=dict)


def pos(x: int, row: int) -> int:
    return x | (row << 10)


def unpos(p: int):
    return p & 0x3ff, p >> 10


def iter_program_data(x) -> Iterator[bytes]:
    """xgwx 안의 모든 <ProgramData> 블록을 풀어 차례로 낸다."""
    for m in xgwx_container._PROGRAM_RE.finditer(x.xml):
        yield bz2.decompress(base64.b64decode(m.group(2)))


# ---------------------------------------------------------------- 읽기

class _Reader:
    def __init__(self, data: bytes):
        self.d = data
        self.i = 0

    def take(self, n):
        if self.i + n > len(self.d):
            raise ValueError("데이터가 짧음 @%#x" % self.i)
        b = self.d[self.i:self.i + n]
        self.i += n
        return b

    def u8(self):
        return self.take(1)[0]

    def u16(self):
        return struct.unpack("<H", self.take(2))[0]

    def u32(self):
        return struct.unpack("<I", self.take(4))[0]

    def cstr(self):
        at = self.i
        if self.take(3) != b"\xff\xfe\xff":
            raise ValueError("CString 아님 @%#x" % at)
        n = self.u8()
        if n == 0xff:
            raise ValueError("긴 CString 미지원 @%#x" % at)
        return self.take(2 * n).decode("utf-16le")


def _read_elem(r: _Reader) -> Element:
    at = r.i
    tag = r.u8()
    kind = r.u32()
    f = {"tag": tag}
    if kind == 0x00 and tag == 0x00:          # VLINE 본체
        links = []
        for _ in range(r.u16()):
            x, y = unpos(r.u32())
            links.append({"x": x, "y": y, "_raw_a": r.u16(),
                          "_raw_b": r.u32()})
        f["links"] = links
        x, y = (links[0]["x"], links[0]["y"]) if links else (0, 0)
        return Element(kind, x, y, f)
    x, y = unpos(r.u32())
    if tag in (0x01, 0x02):                    # VLINE 참조, BOX 부분
        return Element(kind, x, y, f)
    if tag == 0xff and kind == 0x43:           # ROW 머리
        f["_raw_pos"] = x | (y << 10)
        f["comment_row"] = r.u32()
        f["_raw_a"] = r.u32()
        f["right"] = r.u32()
        f["_raw_c"] = r.u32()
        f["_raw_d"] = r.u32()
        f["_n"] = r.u16()
        return Element(kind, 0, 0, f)
    f["_raw_a"] = r.u16()
    f["flags"] = r.u32()
    if tag == 0x00 and kind == 0x22:           # BOX
        f["code"] = r.u32()
        f["text"] = r.cstr()
        ops = []
        for _ in range(r.u16()):
            ox, oy = unpos(r.u32())
            ops.append({"x": ox, "y": oy, "_raw_a": r.u16(),
                        "flags": r.u32(), "text": r.cstr()})
        f["ops"] = ops
    elif tag == 0xff and kind == 0x02:         # HLINE
        f["end_x"], f["end_y"] = unpos(r.u32())
    elif tag == 0xff and kind == 0x3f:         # RUNG_COMMENT
        f["text"] = r.cstr()
        f["_raw_tail"] = r.take(8)
    elif tag == 0xff and f["_raw_a"] == 1:     # 접점·코일
        f["device"] = r.cstr()
    else:
        raise ValueError("모르는 요소 tag=%#x kind=%#x @%#x" % (tag, kind, at))
    return Element(kind, x, y, f)


def decode(raw: bytes) -> List[RungRec]:
    r = _Reader(raw)
    if r.u32() != 0:
        raise ValueError("프로그램 머리 첫 u32가 0이 아님")
    r.u16()                                     # 총행수 (encode가 다시 셈)
    n_rungs = r.u16()
    rungs = []
    for _ in range(n_rungs):
        index = r.u32()
        flag = r.u32()
        elements = []
        comment = ""
        for _ in range(r.u16()):
            row = r.u32()
            head = _read_elem(r)
            if head.kind != 0x43 or head.fields["tag"] != 0xff:
                raise ValueError("ROW 머리가 아님 @%#x" % r.i)
            head.y = row
            n = head.fields.pop("_n")
            elements.append(head)
            for _ in range(n):
                e = _read_elem(r)
                if e.kind == 0x3f:
                    comment = e.fields["text"]
                elements.append(e)
        rungs.append(RungRec(index, elements, comment, {"_raw_flag": flag}))
    if r.i != len(raw):
        raise ValueError("남는 바이트 %d개" % (len(raw) - r.i))
    return rungs


# ---------------------------------------------------------------- 쓰기

def _cstr(s: str) -> bytes:
    # 길이 바이트는 UTF-16 단위 수다(읽기 쪽 cstr()과 같음). BMP 밖 문자는 2단위.
    b = s.encode("utf-16le")
    n = len(b) // 2
    if n >= 0xff:
        raise ValueError("CString이 너무 김 (UTF-16 %d단위)" % n)
    return b"\xff\xfe\xff" + bytes([n]) + b


def _default_tag(kind):
    return {0x00: 0x00, 0x22: 0x00, 0x23: 0x01, 0x24: 0x02}.get(kind, 0xff)


def _default_ops(e: Element):
    parts = e.fields["text"].split(",")
    if parts[0] == "END":
        xs = [e.x]
    else:
        xs = [e.x + 3 * i for i in range(len(parts))]
    flags = e.fields.get(
        "flags", 0 if parts[0] in _COMPARE else 0x20)
    return [{"x": x, "y": e.y, "_raw_a": 1 if i == 0 else 0,
             "flags": flags, "text": t}
            for i, (x, t) in enumerate(zip(xs, parts))]


def _write_elem(e: Element) -> bytes:
    f = e.fields
    tag = f.get("tag", _default_tag(e.kind))
    out = struct.pack("<BI", tag, e.kind)
    if e.kind == 0x00 and tag == 0x00:
        links = f.get("links") or [{"x": e.x, "y": e.y},
                                   {"x": e.x - 1, "y": e.y + 1}]
        out += struct.pack("<H", len(links))
        for k in links:
            out += struct.pack("<IHI", pos(k["x"], k["y"]),
                               k.get("_raw_a", 0), k.get("_raw_b", 0))
        return out
    out += struct.pack("<I", pos(e.x, e.y))
    if tag in (0x01, 0x02):
        return out
    is_box = tag == 0x00 and e.kind == 0x22
    if e.kind == 0x02 or e.kind == 0x3f:
        a = 0
    else:
        a = 1
    if e.kind in _OUTPUTS or e.kind == 0x3f:
        flags = 0x20
    elif is_box:
        # 비교 상자(=,<,> …)는 입력 쪽이라 flags 0, 나머지 상자는 0x20
        flags = 0 if f["text"].split(",")[0] in _COMPARE else 0x20
    else:
        flags = 0
    out += struct.pack("<HI", f.get("_raw_a", a), f.get("flags", flags))
    if is_box:
        text = f["text"]
        code = f.get("code", BOX_CODE.get(text.split(",")[0]))
        if code is None:
            raise ValueError("상자 code를 모름: %s" % text)
        ops = f.get("ops") or _default_ops(e)
        out += struct.pack("<I", code) + _cstr(text)
        out += struct.pack("<H", len(ops))
        for o in ops:
            out += struct.pack("<IHI", pos(o["x"], o["y"]),
                               o.get("_raw_a", 0), o.get("flags", 0x20))
            out += _cstr(o["text"])
    elif e.kind == 0x02:
        out += struct.pack("<I", pos(f["end_x"], f.get("end_y", e.y)))
    elif e.kind == 0x3f:
        out += _cstr(f["text"]) + f.get("_raw_tail", _DEFAULT_COMMENT_TAIL)
    else:
        out += _cstr(f["device"])
    return out


def _row_of(e: Element) -> int:
    if e.kind == 0x00 and e.fields.get("tag", 0) == 0x01:
        return e.y + 1                          # 참조는 아래 행에 들어감
    return e.y


def _auto_rows(elements: List[Element]) -> List[Element]:
    """ROW 머리가 없는 렁: 행으로 나누고 머리와 상자 부분을 채운다."""
    rows = {}
    for e in elements:
        rows.setdefault(_row_of(e), []).append(e)
    out = []
    for row in sorted(rows):
        els = []
        # x 오름차순, 같은 x면 세로선 참조(tag 1)가 본체보다 앞 (표본 규칙)
        ordered = sorted(rows[row], key=lambda e: (
            e.x, 0 if (e.kind == 0x00 and e.fields.get("tag", 0) == 1)
            else 1))
        for e in ordered:
            if e.kind in (0x23, 0x24):
                continue                       # 상자 부분은 아래에서 다시 붙인다
            els.append(e)
            if (e.kind == 0x22 and e.fields.get("tag", 0) == 0x00
                    and e.fields["text"].split(",")[0] != "END"):
                els.append(Element(0x23, e.x, e.y, {"tag": 0x01}))
                els.append(Element(0x24, e.x, e.y, {"tag": 0x02}))
        right = [e.x for e in els
                 if e.kind in _OUTPUTS or (e.kind == 0x22 and e.x >= BOX_X)]
        head = Element(0x43, 0, row, {
            "comment_row": int(any(e.kind == 0x3f for e in els)),
            "right": pos(min(right) if right else OUT_X, row),
        })
        out.append(head)
        out.extend(els)
    return out


def encode(rungs: List[RungRec]) -> bytes:
    body = b""
    max_row = -1
    for rung in rungs:
        els = rung.elements
        if not any(e.kind == 0x43 for e in els):
            els = _auto_rows(els)
        rows = []
        for e in els:
            if e.kind == 0x43:
                rows.append((e, []))
            elif not rows:
                raise ValueError("ROW 머리 앞에 요소가 있음")
            else:
                rows[-1][1].append(e)
        texts = [e.fields["text"] for _, es in rows for e in es
                 if e.kind == 0x3f]
        if rung.comment and rung.comment not in texts:
            raise ValueError("comment와 RUNG_COMMENT 요소가 다름")
        body += struct.pack("<IIH", rung.index,
                            rung.fields.get("_raw_flag", 0), len(rows))
        for head, es in rows:
            f = head.fields
            row = head.y
            max_row = max(max_row, row)
            body += struct.pack("<I", row)
            body += struct.pack(
                "<BIIIIIIIH", 0xff, 0x43, f.get("_raw_pos", 0),
                f.get("comment_row", 0), f.get("_raw_a", 39),
                f.get("right", pos(OUT_X, row)),
                f.get("_raw_c", pos(OUT_X, row)),
                f.get("_raw_d", pos(1, row)), len(es))
            for e in es:
                body += _write_elem(e)
    return struct.pack("<IHH", 0, max_row + 1, len(rungs)) + body


# ------------------------------------------------- 이름으로 요소 만들기
# KIND는 저장되는 kind 코드라 TON/END/BOX가 모두 0x22다(되짚기 불가).
# Task 6은 아래 make_* 로 요소를 만들고, 이름이 필요하면 name_of()를 쓴다.

def make_contact(dev: str, x: int, y: int, neg: bool = False,
                 edge: bool = False) -> Element:
    """a접점(기본), b접점(neg), 상승 펄스 접점(edge; 0x08, 추정)."""
    if neg and edge:
        raise ValueError("하강/반전 펄스 접점은 표본에 없음")
    kind = 0x08 if edge else 0x07 if neg else 0x06
    return Element(kind, x, y, {"device": dev})


def make_coil(dev: str, y: int, x: int = OUT_X) -> Element:
    return Element(0x0e, x, y, {"device": dev})


def make_set(dev: str, y: int, x: int = OUT_X) -> Element:
    return Element(0x10, x, y, {"device": dev})


def make_rst(dev: str, y: int, x: int = OUT_X) -> Element:
    return Element(0x11, x, y, {"device": dev})


def make_ton(dev: str, preset, y: int, x: int = BOX_X) -> Element:
    """TON 상자(x=88~94 세 칸). 앞 가로선은 end_x=85까지."""
    return Element(0x22, x, y, {"text": "TON,%s,%s" % (dev, preset),
                                "code": BOX_CODE["TON"]})


def make_end(y: int, x: int = OUT_X) -> Element:
    """END 상자. 렁은 make_hline(1, y, 91) + make_end(y)."""
    return Element(0x22, x, y, {"text": "END", "code": BOX_CODE["END"]})


def make_box(text: str, x: int, y: int) -> Element:
    """명령 상자(MOV, BRST, TON, 비교 = > < >= <= …). code는 첫 낱말로 정한다.

    출력 쪽 상자는 x=88, 입력 쪽 비교 상자는 접점 칸의 x에 둔다.
    피연산자와 BOX_PART1/2는 encode가 채운다(_default_ops, _auto_rows)."""
    op = text.split(",")[0]
    if op not in BOX_CODE:
        raise ValueError("상자 code를 모름: %s" % text)
    return Element(0x22, x, y, {"text": text, "code": BOX_CODE[op]})


def make_hline(x: int, y: int, end_x: int) -> Element:
    return Element(0x02, x, y, {"end_x": end_x})


def make_vline(x: int, y: int) -> List[Element]:
    """x(=3c+3)에서 y행과 y+1행을 잇는 세로선: [본체(y행), 참조(y+1행)]."""
    return [Element(0x00, x, y, {"tag": 0x00}),
            Element(0x00, x, y, {"tag": 0x01})]


def make_comment(text: str, y: int) -> Element:
    """렁 설명. 혼자 한 렁(RungRec(i, [make_comment(...)], text))."""
    return Element(0x3f, 1, y, {"text": text})


_NAMES = {0x02: "HLINE", 0x06: "CONTACT_NO", 0x07: "CONTACT_NC",
          0x08: "CONTACT_P", 0x0e: "COIL", 0x10: "SET", 0x11: "RST",
          0x23: "BOX_PART1", 0x24: "BOX_PART2", 0x3f: "RUNG_COMMENT",
          0x43: "ROW"}


def name_of(e: Element) -> str:
    """요소의 뜻 이름. 상자는 명령어로 구별: "TON", "END", 그 밖은 "BOX:MOV"."""
    if e.kind == 0x00:
        return "VLINE" if e.fields.get("tag", 0) == 0 else "VLINE_REF"
    if e.kind == 0x22:
        op = e.fields["text"].split(",")[0]
        return op if op in ("TON", "END") else "BOX:" + op
    return _NAMES.get(e.kind, "UNKNOWN_%#x" % e.kind)
