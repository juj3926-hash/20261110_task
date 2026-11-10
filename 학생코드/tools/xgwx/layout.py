"""래더 원본(ladder_src.Rung)을 XG5000 LD 렁(codec.RungRec)으로 배치한다.

배치 규칙 (칸 c의 x = 3c+1. 근거: 555 렁 0·1·3·7·9·10, 055 비교 렁 — tests/test_xgwx_layout):
- 접점은 한 칸(a 0x06, b 0x07, 상승 에지 0x08), 비교 접점은 세 칸(make_box "=,D01000,1111").
  조건이 쓰는 칸 수를 k라 한다.
- 동작 1개·가지 1개: x=3k+1부터 가로선(코일·SET·RST 앞이면 91, 상자 앞이면 85까지),
  출력은 코일 x=94, 상자(TON/MOV/BRST) x=88. 가로선 칸이 없으면 생략.
- 동작 n개(n≥2)·가지 1개(555 렁 1·3): 행 y0+i(i<n-1)마다 세로선 make_vline(3k, y0+i),
  행마다 x=3k+1부터 그 행 동작의 끝 x까지 가로선과 동작.
- 가지 여러 개·동작 1개: 과제 1 xgwx_writer 규칙(555 렁 10).
- 칸 한도: 상자 동작이 있으면 k ≤ 28, 없으면 k ≤ 30. 넘으면 ValueError.
- 렁 설명은 그 렁 바로 위에 혼자 한 렁. 마지막에 END 렁. 렁·행 번호는 0부터 연속.
ROW 머리, 상자 부분, 세로선 링크, 비교 상자 피연산자는 codec.encode가 채운다.
"""

from typing import List

from tools.xgwx import codec
from tools.xgwx.codec import (
    RungRec, make_box, make_coil, make_comment, make_contact, make_end, make_hline,
    make_rst, make_set, make_vline)
from tools.xgwx.ladder_src import Cmp

OUT_COL = 31          # 코일/SET/RST 칸 (x=94)
BOX_COL = 29          # 상자 첫 칸 (x=88)
_BOX_OPS = ("TON", "MOV", "BRST")


def _x(col: int) -> int:
    return 3 * col + 1


def _cells(branch) -> int:
    return sum(3 if isinstance(lit, Cmp) else 1 for lit in branch)


def _condition(branch, y) -> list:
    els, c = [], 0
    for lit in branch:
        if isinstance(lit, Cmp):
            els.append(make_box("%s,%s,%s" % (lit.op, lit.left, lit.right), _x(c), y))
            c += 3
        else:
            els.append(make_contact(lit.dev, _x(c), y, neg=lit.neg, edge=lit.edge))
            c += 1
    return els


def _out_col(action) -> int:
    return BOX_COL if action.op in _BOX_OPS else OUT_COL


def _action(a, y):
    if a.op == "OUT":
        return make_coil(a.args[0], y)
    if a.op == "SET":
        return make_set(a.args[0], y)
    if a.op == "RST":
        return make_rst(a.args[0], y)
    if a.op in _BOX_OPS:
        return make_box(",".join((a.op,) + tuple(a.args)), _x(BOX_COL), y)
    raise ValueError("모르는 동작: %s" % a.op)


def _rung_elements(rung, y0) -> list:
    k = max(_cells(b) for b in rung.branches)
    limit = min(_out_col(a) for a in rung.actions) - 1
    if k > limit:
        raise ValueError("%d행: 조건이 %d칸이라 출력 앞 %d칸을 넘음" % (rung.line, k, limit))
    els = []
    if len(rung.branches) == 1:
        els += _condition(rung.branches[0], y0)
        if len(rung.actions) == 1:
            a = rung.actions[0]
            if k < _out_col(a):
                els.append(make_hline(_x(k), y0, _x(_out_col(a) - 1)))
            els.append(_action(a, y0))
            return els
        n = len(rung.actions)
        for i, a in enumerate(rung.actions):
            y = y0 + i
            if i < n - 1:
                els.extend(make_vline(3 * k, y))
            els.append(make_hline(_x(k), y, _x(_out_col(a) - 1)))
            els.append(_action(a, y))
        return els
    # 가지 여러 개, 동작 1개 (과제 1 규칙)
    a = rung.actions[0]
    out_col = _out_col(a)
    last = len(rung.branches) - 1
    for i, branch in enumerate(rung.branches):
        y = y0 + i
        els += _condition(branch, y)
        bk = _cells(branch)
        if bk < k:
            els.append(make_hline(_x(bk), y, _x(k - 1)))
        if i < last:
            els.extend(make_vline(3 * k, y))
        if i == 0 and k < out_col:
            els.append(make_hline(_x(k), y, _x(out_col - 1)))
    els.append(_action(a, y0))
    return els


def layout(rungs) -> List[RungRec]:
    out = []
    y = 0
    for rung in rungs:
        if rung.comment:
            out.append(RungRec(len(out), [make_comment(rung.comment, y)], rung.comment))
            y += 1
        out.append(RungRec(len(out), _rung_elements(rung, y)))
        y += max(len(rung.branches), len(rung.actions))
    out.append(RungRec(len(out), [make_hline(1, y, 91), make_end(y)]))
    return out


def shape(rung: RungRec) -> list:
    """비교용 모양: (이름, x, 렁 첫 행 기준 y, device 또는 text, end_x). ROW 머리 제외, 순서 그대로."""
    heads = [e.y for e in rung.elements if e.kind == 0x43]
    y0 = min(heads) if heads else min(e.y for e in rung.elements)
    return [(codec.name_of(e), e.x, e.y - y0,
             e.fields.get("device") or e.fields.get("text"), e.fields.get("end_x"))
            for e in rung.elements if e.kind != 0x43]


def encoded(rungs: List[RungRec]) -> List[RungRec]:
    """encode 뒤 decode — 저장 순서(행별 x 오름차순)로 맞춰 비교할 때 쓴다."""
    return codec.decode(codec.encode(rungs))
