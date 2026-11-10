"""출처: method.system_programming/task1_ladder/tools/xgwx_container.py (2026-11-03)

LS XG5000 프로젝트 파일(.xgwx) 컨테이너 읽기/쓰기.

파일 구조 (C:\\XG5000\\Projects 아래 표본 16개, 서로 다른 내용 8종으로 확인)::

    [헤더 138바이트][gzip 블록 n바이트(4바이트 경계까지 0 채움)][꼬리]

헤더 필드 (정수는 모두 little-endian):

| offset | size | 값/뜻 | 확인 방법 |
|--------|------|-------|-----------|
| 0x00 | 2  | `XG` 시그니처 | 표본 전부 같음 |
| 0x02 | 4  | `00 04 02 00` (버전/형식으로 추정, 뜻 미확인) | 표본 전부 같음, 그대로 둔다 |
| 0x06 | 46 | MFC CString: `ff fe ff` + 길이(0x15) + UTF-16LE "XG5000 WORKSPACE FILE" | 표본 전부 같음 |
| 0x34 | 4  | u32 = 1 (뜻 미확인) | 표본 전부 같음 |
| 0x38 | 4  | 빈 MFC CString `ff fe ff 00` | 표본 전부 같음 |
| 0x3c | 4  | 빈 MFC CString `ff fe ff 00` | 표본 전부 같음 |
| 0x40 | 4  | u32 체크섬 = sum(gzip 블록 n바이트) + n | 8종 모두 `v - sum(blk) == n` 성립 |
| 0x44 | 66 | 0으로 채움 | 표본 전부 0 |
| 0x86 | 4  | u32 n = gzip 블록 길이. gzip 실제 길이를 4의 배수로 올린 값, 남는 칸은 0 | 8종 모두 `n == (gzip길이+3)&~3`, 채움 바이트 0 |

gzip 블록: 헤더 `1f 8b 08 00 00000000 00 0b` (FLG=0, MTIME=0, XFL=0, OS=11 NTFS)
+ raw deflate + CRC32 + ISIZE. 안에는 XML 프로젝트 문서(UTF-8 선언이지만 일부
이름이 CP949 → bytes로만 다룬다).

꼬리(trailer, 그대로 보존): 빈 CString `ff fe ff 00` + u32 길이 + 시그니처를
`PK`→`XG`로 바꾼 ZIP(`~fileIndex/<GUID>.idx`) + u32 길이(0xc0) + 또 다른 gzip
블록. XML과 무관해 보여 손대지 않는다(템플릿 꼬리를 그대로 써도 되는지는 Task 6).

ProgramData: `<ProgramData ... Compressed="1" dt:dt="bin.base64">` 안의 base64
(72자마다 CRLF, 끝에는 줄바꿈 없음)를 풀면 bzip2(`BZh9`) 스트림이다.

XML이 바뀌지 않았으면 원본 gzip 블록을 그대로 다시 쓴다(XG5000의 deflate 출력을
재현할 수 없으므로 바이트 동일 왕복을 위해 필요).
"""

import base64
import bz2
import dataclasses
import re
import struct
import zlib
from typing import Optional

HEADER_LEN = 0x8a
OFF_CHECKSUM = 0x40
OFF_LENGTH = 0x86
GZIP_HEADER = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x0b"
B64_LINE = 72

_PROGRAM_RE = re.compile(rb"(<ProgramData\b[^>]*>)(.*?)(</ProgramData>)", re.S)


@dataclasses.dataclass
class Xgwx:
    header: bytes
    xml: bytes
    trailer: bytes
    # 읽어 온 원본 gzip 블록(채움 포함). xml과 맞을 때만 write에서 재사용한다.
    gzip_block: Optional[bytes] = dataclasses.field(
        default=None, compare=False, repr=False)


def _inflate(block: bytes) -> bytes:
    z = zlib.decompressobj(31)
    xml = z.decompress(block)
    if not z.eof:
        raise ValueError("gzip 스트림이 끝나지 않음")
    if z.unused_data.strip(b"\0"):
        raise ValueError("gzip 뒤에 0이 아닌 바이트")
    return xml


def read(path) -> Xgwx:
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:2] != b"XG" or len(data) < HEADER_LEN:
        raise ValueError("xgwx 헤더가 아님: %s" % path)
    header = data[:HEADER_LEN]
    n = struct.unpack_from("<I", header, OFF_LENGTH)[0]
    block = data[HEADER_LEN:HEADER_LEN + n]
    if len(block) != n:
        raise ValueError("gzip 블록 길이 부족")
    return Xgwx(header, _inflate(block), data[HEADER_LEN + n:], block)


def _deflate(xml: bytes) -> bytes:
    c = zlib.compressobj(9, zlib.DEFLATED, -15)
    body = c.compress(xml) + c.flush()
    gz = GZIP_HEADER + body + struct.pack(
        "<II", zlib.crc32(xml) & 0xffffffff, len(xml) & 0xffffffff)
    return gz + b"\0" * (-len(gz) % 4)


def write(x: Xgwx, path) -> None:
    block = x.gzip_block
    if block is None or _inflate(block) != x.xml:
        block = _deflate(x.xml)
    n = len(block)
    header = bytearray(x.header)
    struct.pack_into("<I", header, OFF_LENGTH, n)
    struct.pack_into("<I", header, OFF_CHECKSUM, (sum(block) + n) & 0xffffffff)
    with open(path, "wb") as fh:
        fh.write(bytes(header) + block + x.trailer)


_PROGRAM_NAME_RE = re.compile(rb"<Program\b[^>]*>([^<]*)<Body\b")


def program_names(x: Xgwx) -> list:
    """<Program …>이름<Body … 의 이름들, 파일 순서대로."""
    return [m.group(1).decode("utf-8") for m in _PROGRAM_NAME_RE.finditer(x.xml)]


def _data_match(x: Xgwx, name: Optional[str]):
    """name이 없으면 첫 ProgramData, 있으면 그 이름의 <Program> 안의 ProgramData.
    그 이름의 프로그램이 정확히 하나가 아니면 ValueError (이 저장소에서 추가)."""
    if name is None:
        m = _PROGRAM_RE.search(x.xml)
    else:
        pat = re.compile(rb"<Program\b[^>]*>" + re.escape(name.encode("utf-8"))
                         + rb"<Body\b.*?</Program>", re.S)
        spans = [p.span() for p in pat.finditer(x.xml)]
        if len(spans) != 1:
            raise ValueError("프로그램 '%s'이(가) %d개 — 정확히 하나여야 함 (있는 것: %s)"
                             % (name, len(spans), program_names(x)))
        m = _PROGRAM_RE.search(x.xml, *spans[0])
    if m is None:
        raise ValueError("ProgramData 없음")
    return m


def get_program_data(x: Xgwx, name: Optional[str] = None) -> bytes:
    m = _data_match(x, name)
    return bz2.decompress(base64.b64decode(m.group(2)))


def _wrap(b64: bytes) -> bytes:
    return b"\r\n".join(b64[i:i + B64_LINE]
                        for i in range(0, len(b64), B64_LINE))


def set_program_data(x: Xgwx, raw: bytes, name: Optional[str] = None) -> Xgwx:
    m = _data_match(x, name)
    body = _wrap(base64.b64encode(bz2.compress(raw, 9)))
    xml = x.xml[:m.start(2)] + body + x.xml[m.end(2):]
    return dataclasses.replace(x, xml=xml)
