"""래더 원본(텍스트) 파서. 시뮬레이터(sim)와 배치기(layout)가 공유한다.

과제 1의 원본 형식(한 줄 = 렁 하나, a·b·상승 에지 접점, ``|`` 가지)을 넓혔다:
비교 접점, MOV·BRST, 한 렁에 동작 여러 개, 기호(MAP).

문법 (앞뒤 공백 무시, 빈 줄과 ``#`` 줄은 건너뜀)::

    MAP <기호> <디바이스> [; 설명]
    <동작>[, <동작>…] := <가지> [| <가지>…] [; 렁 설명]

    동작:   OUT d | SET d | RST d       d = P·M 비트
            TON t n                     t = T, n = 정수
            MOV s d                     s = 정수 또는 D, d = D
            BRST d n                    d = P·M 비트, n = 1~16
    가지:   리터럴을 '&'로 잇는다
    리터럴: d | !d | ^d                 d = P·M·T·F 비트
            [op w v]                    op = > < >= <=, w = D·T, v = 정수 또는 D

기호는 파싱할 때 디바이스로 바꾼다. 그래서 Rung 안에는 디바이스만 있다.
"""

import re
from dataclasses import dataclass, field
from typing import Union

# P·M·F는 워드 4자리 10진 + 비트 1자리 16진(M0031F 다음은 M00320). T는 4자리, D는 5자리 10진.
DEVICE_RE = re.compile(r"^([PMF]\d{4}[0-9A-F]|T\d{4}|D\d{5})$")
_DEVICE_LIKE = re.compile(r"^[PMFTD][0-9A-F]+$")   # 디바이스처럼 생겼지만 형식이 틀림
_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_INT_RE = re.compile(r"^\d+$")
COMPARE_OPS = ("=", ">", "<", ">=", "<=")
MAX_COMMENT = 200


class ParseError(Exception):
    pass


@dataclass(frozen=True)
class Lit:
    dev: str
    neg: bool = False
    edge: bool = False


@dataclass(frozen=True)
class Cmp:
    op: str
    left: str
    right: str


@dataclass(frozen=True)
class Action:
    op: str
    args: tuple


@dataclass
class Rung:
    actions: list
    branches: list
    comment: str
    line: int


@dataclass
class Source:
    symbols: dict = field(default_factory=dict)
    rungs: list = field(default_factory=list)


Literal = Union[Lit, Cmp]


def bits_from(dev: str, n: int) -> list:
    """P·M 비트 dev부터 n개. 워드 번호(4자리 10진) + 비트(16진 1자리)로 센다.

    예: M0031E, 3 → [M0031E, M0031F, M00320]."""
    idx = int(dev[1:5]) * 16 + int(dev[5], 16)
    return ["%s%04d%X" % (dev[0], i // 16, i % 16) for i in range(idx, idx + n)]


def _err(n, msg):
    return ParseError("%d행: %s" % (n, msg))


class _Parser:
    def __init__(self):
        self.symbols = {}

    def dev(self, tok, n, kinds, what):
        if DEVICE_RE.match(tok):
            d = tok
        elif _DEVICE_LIKE.match(tok):
            raise _err(n, "%s: 잘못된 디바이스 '%s'" % (what, tok))
        elif _SYMBOL_RE.match(tok):
            if tok not in self.symbols:
                raise _err(n, "%s: MAP하지 않은 기호 '%s'" % (what, tok))
            d = self.symbols[tok]
        else:
            raise _err(n, "%s: 잘못된 디바이스 '%s'" % (what, tok))
        if d[0] not in kinds:
            raise _err(n, "%s: %s 디바이스가 아님 '%s'" % (what, "·".join(kinds), tok))
        return d

    def int_or_dev(self, tok, n, kinds, what):
        return tok if _INT_RE.match(tok) else self.dev(tok, n, kinds, what)

    def map_line(self, rest, n):
        parts = rest.split()
        if len(parts) != 2:
            raise _err(n, "MAP은 'MAP 기호 디바이스' 형식")
        sym, dev = parts
        if not _SYMBOL_RE.match(sym) or DEVICE_RE.match(sym) or _DEVICE_LIKE.match(sym):
            raise _err(n, "MAP: 기호 이름으로 쓸 수 없음 '%s'" % sym)
        if not DEVICE_RE.match(dev):
            raise _err(n, "MAP: 잘못된 디바이스 '%s'" % dev)
        if sym in self.symbols:
            raise _err(n, "MAP: 기호 '%s'를 다시 정의함" % sym)
        self.symbols[sym] = dev

    def action(self, text, n):
        parts = text.split()
        if not parts:
            raise _err(n, "동작이 비어 있음")
        op, args = parts[0], parts[1:]
        want = {"OUT": 1, "SET": 1, "RST": 1, "TON": 2, "MOV": 2, "BRST": 2}
        if op not in want:
            raise _err(n, "알 수 없는 동작 '%s'" % op)
        if len(args) != want[op]:
            raise _err(n, "%s: 인자 개수가 맞지 않음" % op)
        if op in ("OUT", "SET", "RST"):
            return Action(op, (self.dev(args[0], n, "PM", op + " 대상"),))
        if op == "TON":
            t = self.dev(args[0], n, "T", "TON 타이머")
            if not _INT_RE.match(args[1]):
                raise _err(n, "TON: 설정값이 정수가 아님 '%s'" % args[1])
            return Action(op, (t, args[1]))
        if op == "MOV":
            src = self.int_or_dev(args[0], n, "D", "MOV 원본")
            return Action(op, (src, self.dev(args[1], n, "D", "MOV 대상")))
        d = self.dev(args[0], n, "PM", "BRST 대상")
        if not _INT_RE.match(args[1]) or not 1 <= int(args[1]) <= 16:
            raise _err(n, "BRST: 개수는 1~16 정수 '%s'" % args[1])
        return Action(op, (d, args[1]))

    def literal(self, tok, n):
        if not tok:
            raise _err(n, "조건에 빈 리터럴이 있음")
        if tok.startswith("["):
            if not tok.endswith("]"):
                raise _err(n, "비교: ']'가 없음 '%s'" % tok)
            parts = tok[1:-1].split()
            if len(parts) != 3 or parts[0] not in COMPARE_OPS:
                raise _err(n, "비교: '[op 워드 값]' 형식이 아님 '%s'" % tok)
            op, w, v = parts
            return Cmp(op, self.dev(w, n, "DT", "비교"), self.int_or_dev(v, n, "D", "비교"))
        neg = edge = False
        if tok.startswith("!"):
            neg, tok = True, tok[1:].strip()
        elif tok.startswith("^"):
            edge, tok = True, tok[1:].strip()
        return Lit(self.dev(tok, n, "PMTF", "접점"), neg, edge)

    def rung(self, body, comment, n):
        if ":=" not in body:
            raise _err(n, "':='가 없음 (동작 := 조건)")
        head, _, cond = body.partition(":=")
        actions = [self.action(a.strip(), n) for a in head.split(",")]
        if not cond.strip():
            raise _err(n, "조건이 비어 있음")
        branches = [[self.literal(t.strip(), n) for t in b.split("&")]
                    for b in cond.split("|")]
        if len(branches) > 1 and len(actions) > 1:
            raise _err(n, "가지가 둘 이상이면 동작은 하나만 쓸 수 있음")
        if len(comment) > MAX_COMMENT:
            raise _err(n, "렁 설명이 %d자를 넘음" % MAX_COMMENT)
        if any(ord(ch) > 0xFFFF for ch in comment):
            raise _err(n, "렁 설명에 쓸 수 없는 문자(이모지 등 BMP 밖 문자)가 있음")
        return Rung(actions, branches, comment, n)


def parse(text: str) -> Source:
    p = _Parser()
    rungs = []
    for n, raw in enumerate(text.splitlines(), 1):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        body, _, comment = s.partition(";")
        body, comment = body.strip(), comment.strip()
        if body.startswith("MAP ") or body == "MAP":
            p.map_line(body[3:], n)
            continue
        rungs.append(p.rung(body, comment, n))
    return Source(dict(p.symbols), rungs)


def load(path) -> Source:
    with open(path, encoding="utf-8") as fh:
        return parse(fh.read())


def devices(src: Source) -> set:
    """렁에 나온 모든 디바이스(접점, 비교 피연산자, 동작 인자). BRST는 범위를 펼친다."""
    out = set()
    for r in src.rungs:
        for branch in r.branches:
            for lit in branch:
                if isinstance(lit, Cmp):
                    out.update(t for t in (lit.left, lit.right) if DEVICE_RE.match(t))
                else:
                    out.add(lit.dev)
        for a in r.actions:
            if a.op == "BRST":
                out.update(bits_from(a.args[0], int(a.args[1])))
            else:
                out.update(t for t in a.args if DEVICE_RE.match(t))
    return out
