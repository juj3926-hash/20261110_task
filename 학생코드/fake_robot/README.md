# 가짜 로봇 + 3D 화면 (하위 프로젝트 2)

한화 HCR-3A 대신 PC에서 도는 가짜 로봇이다. 가짜 PLC와 가상 배선(TCP 5021)으로 DI·DO를 주고받고, 팔을 3D로 보여 준다.
규약은 `docs/handoff/virtual-wiring.md`, 동작 규칙은 `docs/handoff/fake-robot-3d.md`를 따른다.

## 실행

```
python -m fake_robot [--plc-host 127.0.0.1] [--wiring-port 5021] [--http-port 8879]
```

- 시작하면 화면 주소와 가짜 PLC 주소를 찍는다. 브라우저로 **http://127.0.0.1:8879** 를 연다.
  `localhost`로 열면 거절된다(이 서버는 127.0.0.1의 Host만 받는다).
- 가짜 PLC가 아직 없어도 된다. 1초마다 다시 접속한다. 연결이 없으면 DI를 모두 0으로 본다.
- 끄기: Ctrl+C. 외부 의존은 없다(파이썬 표준 라이브러리, 저장소에 넣은 브라우저 라이브러리만 쓴다).

## 두 가지 모드

| 모드 | 무엇이 도나 | 브라우저 |
| --- | --- | --- |
| 내장 스크립트 (기본) | 파이썬 `core.Robot`. DI0 → PICK(스크립트1), DI1 → HOME(스크립트2). 0.2초 뒤 DO0 ON, 2초 이동, 요청이 꺼지면 DO0 OFF | 없어도 돈다 |
| 학생 스크립트 | 화면에서 연 Rodi Script `.js`를 브라우저 Web Worker가 돌린다(`static/worker.js` + `rodi.js`) | 탭을 열어 둔다 |

- 학생 스크립트가 도는 동안 내장 스크립트는 DO를 내지 않는다. Worker가 0.05초마다 서버와 주고받는다: 서버가 DI(PLC에서 온 값)를 주고, Worker가 낸 DO 변화를 순서대로 PLC에 보낸다.
- 탭을 닫거나 Worker가 1초 동안 소식이 없으면 서버가 DO를 끈다. 실물도 프로그램이 안 돌면 출력이 LOW다(HCR 가이드 p63).
- 학생 스크립트는 한 탭에서만 돌린다. 백그라운드 탭에서는 브라우저가 화면 갱신을 늦출 수 있다.
- HOME·PICK 관절 값은 화면에서 바꾼다(내장 스크립트용, 다음 이동부터).
- 스크립트 파일을 고친 뒤에는 파일을 다시 고른다. [다시 실행]은 이미 읽어 둔 글을 다시 돌린다.
- WebGL이 없으면 3D 그림 대신 안내 문구가 나온다. 나머지 화면은 그대로 동작한다.

## I/O 계약 (내장 스크립트, 노트 3.1)

- 요청(DI0·DI1)은 켜져 있는지로 본다. 이동이 끝나도 DI0·DI1이 모두 꺼질 때까지 DO0을 켜 두고, 꺼지면 DO0을 끈다(waitAllOff, 2026-11-10 강사 결정). 그 뒤에야 다음 요청을 받는다.
- DI3가 켜지면 0.3초 감속해 멈춘 뒤 DO1 ON, DO0은 그대로. DI3가 꺼지면 DO1 OFF, 남은 이동을 이어 간다.
  목표에 닿아도 DI3가 켜져 있으면 DO0을 끄지 않는다. 쉬는 중이나, 이동이 끝나 요청이 꺼지기를 기다리는 중(DO0 켜짐, 움직이는 것 없음)의 DI3는 바로 DO1 ON이다. DI2는 보지 않는다.
- 참조 스크립트 `robot/PICKNPLACE_FULL_main.js`를 흉내 낸 것이다. 같은 DI 입력 열에 DO0·DO1 변화 순서가 같은지 시험한다(합격 a).

## 학생 Rodi Script

- **ES5만 실행된다.** `var`, `function(){}`, `while` + `sleep`을 쓴다. `let`·`const`·화살표 함수·템플릿 문자열·`class`·기본값 인자가 있으면
  "실물과 다를 수 있음" 경고 뒤에 "문법 오류: ES5만 실행됨 (let·const·화살표 함수 → var·function으로 바꾸세요)"가 나고 실행하지 않는다.
- 흉내 내는 API는 노트 3.3 표의 9개다: `getGeneralDigitalInput`, `setGeneralDigitalOutput`, `getGeneralDigitalOutput`, `sleep(ms)`, `wait(ms | 조건 함수)`,
  `moveJoint(관절, 속도, 가속)`, `moveStop(초)`, `Thread(함수)`, `getCurrentJoint()`. 그 밖의 이름을 부르면(`moveLinear`, `console.log`, `halt`, `popup` …) 실행 전에 "미지원" 오류다.
- `sleep`·`wait`는 ms로 본다. 인자가 10보다 작으면 "초가 아니라 ms일 수 있음" 경고를 낸다.
- `moveJoint`는 막는 호출이다. `moveStop(t)`는 t ≥ 0.01이면 t초(최대 0.5초), 그보다 작으면 0.3초 동안 감속하고, 멈출 때까지 부른 쪽을 기다리게 한다.
- `Thread`는 메인과 번갈아 도는 두 번째 흐름이다. 메인이 `moveJoint`로 막혀도 돈다.
- **실물에서 확인되지 않은 것** (화면과 로그에 경고로 나온다)
  - 스레드에서 `moveStop`을 부를 수 있는지 (HCR 가이드 p184~185: 스레드에서 이동 명령 불가라는 문장이 있다)
  - 스레드가 메인의 전역 변수를 보는지. 흉내에서는 보이지만 실물은 모른다. 그래서 스레드가 스크립트의 전역 변수를 읽거나 쓰면
    이름마다 "변수 공유는 실물 미확인" 경고를 낸다. 읽기만 하는 상수(`DI_STOP` 같은)도 경고한다. 참조 스크립트도 이 경고가 나온다.
- 스크립트가 끝나거나 오류가 나면 모든 흐름을 멈추고 DO를 0으로 한다.

### 참조 스크립트의 HOME·PICK

`robot/PICKNPLACE_FULL_main.js`의 `HOME`(`[0, 0, 90, 0, 90, 0]`)과 `PICK`(`[30, 0, 90, 0, 90, 0]`)은 자리값이다(티칭 값으로 바꿀 것).
둘 사이 이동은 1.5초쯤 걸린다. 이미 목표 위치에서 스크립트를 부르면(예: 스크립트1을 두 번) 이동은 거의 0초지만,
DO0은 요청이 꺼질 때까지 켜져 있으므로 PLC가 놓치지 않는다(2026-11-10 강사 결정).

## 근사인 것

- 팔 모양과 치수: HCR-3A 비슷한 6축 팔이다. 어깨 높이 0.22 m, 위팔 0.27 m, 아래팔 0.23 m 정도로 그렸다(실물 도면이 아니다).
  관절 각도를 그대로 돌릴 뿐 역기구학은 없다. 각도 0의 자세와 돌아가는 방향도 실물과 다를 수 있다.
- 내장 스크립트 이동: 관절 값을 2초 동안 선형 보간한다. 감속은 0.3초 동안 속도를 0까지 고르게 줄인다.
- 학생 스크립트 이동: 가장 많이 움직이는 축이 사다리꼴 속도(최고 `속도` deg/s, 가속 `가속` deg/s²)로 가고 나머지 축이 같은 비율로 따라간다.
  실물의 축별 한계, 블렌딩, 충돌은 흉내 내지 않는다.
- 시간: 파이썬과 Worker 모두 10 ms 간격의 고정 간격 따라잡기 루프다. 컴퓨터가 오래 멈추면 밀린 시간은 버린다.

## 파일

| 파일 | 내용 |
| --- | --- |
| `core.py` | 내장 스크립트1·2와 I/O 계약 (`Robot.step(di, dt)`) |
| `wiring.py` | 가상 배선 JSON 클라이언트 (5021) |
| `server.py`, `__main__.py` | 실시간 루프, HTTP(8879), 학생 스크립트 I/O 다리, 실행 인자 |
| `static/index.html`, `static/app.js` | 3D 화면, 표시등, 상태, 모드, 파일 열기, HOME·PICK, 실행 로그 |
| `static/worker.js`, `static/rodi.js` | 학생 스크립트 Worker와 Rodi Script 실행기 |
| `vendor/` | JS-Interpreter, three.js (버전·출처·라이선스·해시는 `vendor/README.md`) |

## 시험

```
python -m unittest discover -s tests
```

- `tests/test_fake_robot_*.py`. 포트는 모두 0(OS가 고른 포트)으로 연다.
- Node가 있으면 Rodi Script 실행기와 Worker 시험도 돈다(없으면 건너뛴다). Worker는 Node `worker_threads`로 같은 파일을 돌린다.
- 합격 (b)(PC 프로그램 + 가짜 PLC 대역 + 가짜 로봇, tasks 시험 표 1~9행)는 `picknplace_full/app.py`가 있을 때 돈다.
  대역은 가짜 PLC가 아니라 참조 래더를 `tools.xgwx.sim`으로 도는 `tests/fake_robot_samples/doubles.py`의 `PlcDouble`이다.
  그 전에는 PC 쪽 `PlcLink`·`Sequencer`로 한 사이클과 즉시 정지·재개를 참조 래더 대역에 붙여 본다.
- 진짜 세 프로그램(PC 프로그램, 가짜 PLC, 가짜 로봇)으로 시험 표를 도는 것은 `python tests/integration_all.py [--robot builtin|script]`다.
