// PICKNPLACE_FULL 로봇 스크립트 (Rodi Script)
// 초안 — 현장 확인 전. robot/README.md의 확인 목록 참조
// 스크립트 명령 하나에 통째로 붙여 넣는다.
// 문법은 ES5만 쓴다 (var, function, ===, while). let, const, 화살표 함수, 기본값 인자는 쓰지 않는다.
// 스레드와 메인은 I/O(DI3)로만 주고받는다.

// 단자 번호가 함수 번호와 같다고 가정한다 (현장 확인 2)
var DI_MOVE1 = 0;   // 이동1 요청
var DI_MOVE2 = 1;   // 이동2 요청
var DI_STOP = 3;    // 즉시 정지
var DO_BUSY = 0;    // 동작 중
var DO_ABN = 1;     // 비정상 정지

// sleep이 ms 단위라고 가정한다 (현장 확인 1)
var POLL_MS = 20;

var HOME = [0, 0, 90, 0, 90, 0];   // 티칭 값으로 바꿀 것
var PICK = [30, 0, 90, 0, 90, 0];  // 티칭 값으로 바꿀 것
var SPEED = 30;
var ACCEL = 60;
var TOL_DEG = 0.5;

// 시작: 출력을 먼저 끄고 감시 스레드를 시작한다 (DI3가 이미 켜져 있을 때 DO1이 덮어써지지 않게)
setGeneralDigitalOutput(DO_BUSY, 0);
setGeneralDigitalOutput(DO_ABN, 0);

// 감시 스레드: DI3 켜짐 -> moveStop(0) 후 DO1 ON, 꺼짐 -> DO1 OFF
Thread(function () {
  var prev = 0;
  while (true) {
    var now = getGeneralDigitalInput(DI_STOP);
    if (now === 1 && prev === 0) {
      moveStop(0);
      setGeneralDigitalOutput(DO_ABN, 1);
    } else if (now === 0 && prev === 1) {
      setGeneralDigitalOutput(DO_ABN, 0);
    }
    prev = now;
    sleep(POLL_MS);
  }
});

function reached(target) {
  var cur = getCurrentJoint();
  var i;
  for (i = 0; i < target.length; i++) {
    if (Math.abs(cur[i] - target[i]) > TOL_DEG) {
      return false;
    }
  }
  return true;
}

// moveStop 뒤 재개 함수가 없으므로, 목표에 닿을 때까지 같은 목표로 다시 이동한다
function moveJointSafely(target) {
  while (true) {
    while (getGeneralDigitalInput(DI_STOP) === 1) {
      sleep(POLL_MS);
    }
    moveJoint(target, SPEED, ACCEL);
    if (getGeneralDigitalInput(DI_STOP) === 0 && reached(target)) {
      return;
    }
  }
}

// DO0은 요청(DI0·DI1)이 꺼진 뒤에 끈다. 이동이 0초여도 PLC가 DO0을 보고 요청을 끄게 한다 (2026-11-10 강사 결정)
function script1() {
  setGeneralDigitalOutput(DO_BUSY, 1);
  moveJointSafely(PICK);
  waitAllOff();
  setGeneralDigitalOutput(DO_BUSY, 0);
}

function script2() {
  setGeneralDigitalOutput(DO_BUSY, 1);
  moveJointSafely(HOME);
  waitAllOff();
  setGeneralDigitalOutput(DO_BUSY, 0);
}

function waitAllOff() {
  while (getGeneralDigitalInput(DI_MOVE1) === 1 || getGeneralDigitalInput(DI_MOVE2) === 1) {
    sleep(POLL_MS);
  }
}

// 메인
while (true) {
  if (getGeneralDigitalInput(DI_MOVE1) === 1) {
    script1();
  } else if (getGeneralDigitalInput(DI_MOVE2) === 1) {
    script2();
  }
  sleep(POLL_MS);
}
