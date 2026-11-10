"""래더 원본(ladder_src.Rung 목록)을 PLC처럼 스캔 단위로 실행하는 시뮬레이터.

과제 1의 ladder_sim을 넓혔다: 비교 접점, MOV, BRST, 동작 여러 개, 워드, 첫 스캔 플래그.

한 스캔: inputs를 비트에 넣고 → 렁을 위에서 아래로 실행한다.
- 가지의 리터럴은 결과와 관계없이 모두 평가한다(상승 에지 기억을 매 스캔 갱신).
  에지 기억은 리터럴 위치(렁, 가지, 순번)마다 따로 둔다.
- F0009B(첫 스캔)는 첫 scan에서만 1이다.
- OUT은 조건값을 그대로 쓴다. SET·RST·MOV·BRST는 조건이 참일 때만, 적힌 순서대로.
- TON은 조건이 참이면 누적, 거짓이면 0. 누적이 설정값 × 단위 이상이면 완료 비트.
  단위: T0000~T0499 = 100ms, T0500부터 = 10ms (파일 1 기본 파라미터).
"""

from tools.xgwx.ladder_src import Cmp, bits_from

FIRST_SCAN = "F0009B"


def timer_unit_ms(dev: str) -> int:
    return 100 if int(dev[1:]) < 500 else 10


class Plc:
    def __init__(self, rungs):
        self.rungs = rungs
        self.bits = {}       # 디바이스 -> 0/1 (T는 완료 비트)
        self.words = {}      # D 디바이스 -> 정수
        self.acc = {}        # TON 누적 시간(ms)
        self.prev = {}       # (렁, 가지, 순번) -> 이전 값 (상승 에지)
        self.first = True

    def bit(self, dev: str) -> int:
        return self.bits.get(dev, 0)

    def word(self, dev: str) -> int:
        return self.words.get(dev, 0)

    def write_word(self, dev: str, value: int) -> None:
        self.words[dev] = int(value)

    def _value(self, tok: str) -> int:
        if tok.isdigit():
            return int(tok)
        if tok[0] == "T":
            return self.acc.get(tok, 0) // timer_unit_ms(tok)
        return self.word(tok)

    def _cmp(self, c: Cmp) -> int:
        a, b = self._value(c.left), self._value(c.right)
        return int({"=": a == b, ">": a > b, "<": a < b, ">=": a >= b, "<=": a <= b}[c.op])

    def _lit(self, key, lit) -> int:
        if isinstance(lit, Cmp):
            return self._cmp(lit)
        v = self.bit(lit.dev)
        if lit.edge:
            r = int(v == 1 and self.prev.get(key, 0) == 0)
            self.prev[key] = v
            return r
        return int(not v) if lit.neg else v

    def _cond(self, i, rung) -> int:
        result = 0
        for j, branch in enumerate(rung.branches):
            vals = [self._lit((i, j, k), lit) for k, lit in enumerate(branch)]
            result |= int(all(vals))
        return result

    def _do(self, a, c: int, dt_ms: int) -> None:
        if a.op == "OUT":
            self.bits[a.args[0]] = c
        elif a.op == "TON":
            t, preset = a.args
            self.acc[t] = self.acc.get(t, 0) + dt_ms if c else 0
            self.bits[t] = int(c and self.acc[t] >= int(preset) * timer_unit_ms(t))
        elif not c:
            return
        elif a.op == "SET":
            self.bits[a.args[0]] = 1
        elif a.op == "RST":
            self.bits[a.args[0]] = 0
        elif a.op == "MOV":
            self.words[a.args[1]] = self._value(a.args[0])
        elif a.op == "BRST":
            for b in bits_from(a.args[0], int(a.args[1])):
                self.bits[b] = 0
        else:
            raise ValueError("모르는 동작: %s" % a.op)

    def scan(self, inputs=None, dt_ms: int = 10) -> None:
        for dev, v in (inputs or {}).items():
            self.bits[dev] = int(v)
        self.bits[FIRST_SCAN] = int(self.first)
        self.first = False
        for i, rung in enumerate(self.rungs):
            c = self._cond(i, rung)
            for a in rung.actions:
                self._do(a, c, dt_ms)
