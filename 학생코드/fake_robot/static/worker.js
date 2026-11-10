/* 학생 스크립트 Worker (docs/handoff/fake-robot-3d.md 3.3).
 *
 * rodi.js로 스크립트를 실시간으로 돌리고, 서버와 POST /api/io로 I/O를 주고받는다.
 *   페이지 → Worker  {type: 'start', source, joints}, {type: 'stop'}
 *   Worker → 페이지  {type: 'ended'}  스크립트가 끝나고 마지막 소식까지 서버에 보낸 뒤
 * 서버는 DI를 주고 DO 변화를 하나씩 PLC로 보낸다. 페이지는 서버의 /api/state로 그린다.
 */
importScripts('vendor/acorn.js', 'vendor/interpreter.js', 'rodi.js');

var DT_MS = 10;          // 한 step (서버의 실시간 루프와 같다)
var CATCH_UP = 10;       // 한 번 깨어날 때 최대 step. 더 밀리면 버린다
var IO_MS = 50;          // 서버와 주고받는 간격
var MAX_EVENTS = 100;    // 한 번에 보내는 양 (서버의 본문 한도 16 KB 안)
var MAX_LOG = 10;
var MAX_TEXT = 300;

var run = null;
var di = [0, 0, 0, 0];
var events = [['DO0', 0], ['DO1', 0]];   // 서버가 받았다고 답할 때까지 쌓아 둔다. 새 실행은 출력 LOW에서
var log = [];                            // 시작하므로 지난 실행이 남긴 DO를 먼저 끈다
var deadline = 0;

onmessage = function (e) {
  var m = e.data;
  if (m.type === 'start' && !run) {
    run = new RodiRunner(m.source, m.joints);
    deadline = performance.now();
    tick();
    exchange();
  } else if (m.type === 'stop' && run && !run.done) {
    run.end('상태', '멈춤 (사용자)');
  }
};

// 고정 간격 따라잡기 루프 (서버의 fixed_loop와 같다)
function tick() {
  var now = performance.now();
  for (var n = 0; deadline <= now && n < CATCH_UP && !run.done; n++) {
    run.step(di, DT_MS);
    deadline += DT_MS;
  }
  if (deadline <= now) deadline = now + DT_MS;
  if (!run.done) setTimeout(tick, Math.max(0, deadline - performance.now()));
}

// DO 변화 순서·로그·관절·상태를 보내고 DI를 받는다. 실패하면 쌓아 두었다가 다음에 다시 보낸다
function exchange() {
  var finished = run.done;
  events = events.concat(run.events.splice(0));
  log = log.concat(run.log.splice(0).map(function (x) { return [x[0], x[1].slice(0, MAX_TEXT)]; }));
  var sendEvents = events.slice(0, MAX_EVENTS);
  var sendLog = log.slice(0, MAX_LOG);
  fetch('/api/io', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({events: sendEvents, log: sendLog, joints: run.joints, state: run.state()})
  }).then(function (r) {
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
  }).then(function (answer) {
    events.splice(0, sendEvents.length);
    log.splice(0, sendLog.length);
    di = answer.di;
    if (answer.mode !== 'script' && !run.done) run.end('상태', '내장 모드로 바뀌어 멈춤');
    if (finished && !events.length && !log.length) {
      postMessage({type: 'ended'});
    } else {
      setTimeout(exchange, IO_MS);
    }
  }, function () {
    setTimeout(exchange, IO_MS);
  });
}
