/* Rodi Script 실행기 (docs/handoff/fake-robot-3d.md 3.3).
 *
 * 학생 스크립트를 ES5 인터프리터(vendor/interpreter.js)로 한 단계씩 돌린다.
 * 브라우저 Web Worker와 Node(시험)에서 같이 쓴다. 먼저 vendor/acorn.js, vendor/interpreter.js를 불러 둔다.
 *
 *   var run = new RodiRunner(소스, 관절6개);   // 실행 전 검사. 통과하면 실행 준비
 *   run.step(di, dtMs);   // di = [DI0, DI1, …]. dtMs만큼 시간을 보내고 DO 배열(16개)을 돌려준다
 *   run.log      // [종류, 글]. 종류 = 경고 | 오류 | 상태
 *   run.events   // DO 변화 순서 [['DO0', 1], …]
 *   run.joints   // 지금 관절 (deg)
 *   run.state()  // 화면 상태: 쉼 | 동작 중 | 정지 중
 *   run.done     // 끝났거나 오류로 멈춤
 *
 * 시간은 step의 dtMs로만 흐른다. 흐름(메인, 스레드)은 막는 호출(sleep, wait, moveJoint, moveStop)을
 * 만날 때까지 차례로 돈다. 막힌 흐름은 조건(until)이 참이 되면 다음 step에서 다시 돈다.
 */

var RODI_PINS = 16;           // HCR-3A 디지털 입출력 16점씩 (H 4899~4901)
var RODI_STOP_S = 0.3;        // moveStop(0.01 미만)의 자동 감속 시간 = stop_time
var RODI_STEP_LIMIT = 20000;  // 한 흐름이 step 한 번에 도는 최대 단계 (sleep 없는 반복 대비)

// 실물과 다를 수 있는 ES5 밖 문법 (노트 3.3 문법 경고)
var RODI_SYNTAX = [
  ['let', /\blet\b/], ['const', /\bconst\b/], ['=>', /=>/], ['템플릿 문자열', /`/],
  ['class', /\bclass\b/], ['기본값 인자', /\bfunction\b\s*[\w$]*\s*\([^)]*=/]
];

// wait(조건 함수)는 조건을 step마다 다시 본다. 인터프리터 안에서 도는 ES5 함수다
var RODI_WAIT = 'function wait(x) {\n' +
  '  if (typeof x === "function") { while (!x()) { _waitTick(); } } else { _waitMs(x); }\n' +
  '}\n';

function RodiRunner(source, joints) {
  this.now = 0;                 // ms
  this.di = rodiZeros(RODI_PINS);
  this.do = rodiZeros(RODI_PINS);
  this.joints = joints.slice();
  this.log = [];
  this.events = [];
  this.flows = [];
  this.motion = null;
  this.done = false;
  this.warned = {};
  var it = this.interp = new Interpreter(RODI_WAIT, this.api.bind(this));
  it.REGEXP_MODE = 1;   // 정규식은 바로 실행한다 (Worker 안에 Worker를 만들지 않음)
  this.builtin = {};    // 흉내 API와 ES5 내장 이름 (Math 같은 이름은 열거되지 않는다)
  Object.getOwnPropertyNames(it.globalObject.properties).forEach(function (k) { this.builtin[k] = true; }, this);
  var ast = this.check(source);
  if (!ast) {
    this.done = true;
    return;
  }
  it.appendCode(ast);
  this.flows.push({name: '메인', stack: it.getStateStack(), until: null, done: false});
  // 스레드가 전역 변수를 읽고 쓰는 곳을 지켜본다 (변수 공유 경고)
  var self = this, get = it.getValueFromScope, set = it.setValueToScope;
  it.getValueFromScope = function (name) { self.touch(name); return get.call(it, name); };
  it.setValueToScope = function (name, value) { self.touch(name); return set.call(it, name, value); };
  this.say('상태', '실행 시작');
}

function rodiZeros(n) {
  var a = [];
  for (var i = 0; i < n; i++) a.push(0);
  return a;
}

// 실행 전 검사: 문법 경고, ES5 해석, 표 밖 API 호출. 통과하면 AST를 돌려준다
RodiRunner.prototype.check = function (source) {
  // 주석과 따옴표 문자열을 지운다(줄 수는 그대로). ponytail: 정규식 리터럴 안의 따옴표·// 는 못 가린다
  var code = source.replace(/\/\*[\s\S]*?\*\/|\/\/[^\n]*|'(?:\\.|[^'\\\n])*'|"(?:\\.|[^"\\\n])*"/g,
    function (m) { return m.replace(/[^\n]/g, ' '); });
  var first = {}, es6Lines = {};   // 문법마다 처음 나온 줄, ES5 밖 문법이 있는 줄
  code.split('\n').forEach(function (text, i) {
    RODI_SYNTAX.forEach(function (s) {
      if (!s[1].test(text)) return;
      es6Lines[i + 1] = true;
      if (!(s[0] in first)) first[s[0]] = i + 1;
    });
  });
  RODI_SYNTAX.forEach(function (s) {
    if (s[0] in first) this.say('경고', '실물과 다를 수 있음: ' + s[0] + ' (줄 ' + first[s[0]] + ')');
  }, this);
  try {
    var ast = acorn.parse(source, {locations: true, ecmaVersion: 5, sourceFile: 'script'});
  } catch (e) {
    var line = e.loc ? e.loc.line : '?';
    var why = es6Lines[line] ? 'ES5만 실행됨 (let·const·화살표 함수 → var·function으로 바꾸세요)' : e.message;
    this.say('오류', '문법 오류 (줄 ' + line + '): ' + why);
    return null;
  }
  var unknown = this.unknownCalls(ast);
  for (var name in unknown) this.say('오류', '미지원: ' + name + ' (줄 ' + unknown[name] + ')');
  return Object.keys(unknown).length ? null : ast;
};

// 선언되지 않았고 흉내 API·ES5 내장도 아닌 이름을 부르는 곳 {이름: 첫 줄}
RodiRunner.prototype.unknownCalls = function (ast) {
  var known = Object.create(this.builtin), calls = [];
  rodiWalk(ast, function (node) {
    if (node.type === 'VariableDeclarator') known[node.id.name] = true;
    if (node.type === 'FunctionDeclaration' || node.type === 'FunctionExpression') {
      if (node.id) known[node.id.name] = true;
      node.params.forEach(function (p) { known[p.name] = true; });
    }
    if (node.type === 'CatchClause') known[node.param.name] = true;
    if (node.type === 'AssignmentExpression' && node.left.type === 'Identifier') known[node.left.name] = true;
    if (node.type === 'CallExpression' || node.type === 'NewExpression') {
      var callee = node.callee;
      while (callee.type === 'MemberExpression') callee = callee.object;   // console.log → console
      if (callee.type === 'Identifier') calls.push(callee);
    }
  });
  var unknown = {};
  calls.forEach(function (c) {
    if (!known[c.name] && !(c.name in unknown)) unknown[c.name] = c.loc.start.line;
  });
  return unknown;
};

function rodiWalk(node, visit) {
  if (!node || typeof node.type !== 'string') return;
  visit(node);
  for (var k in node) {
    var v = node[k];
    if (k === 'loc' || !v || typeof v !== 'object') continue;
    if (Array.isArray(v)) v.forEach(function (c) { rodiWalk(c, visit); });
    else rodiWalk(v, visit);
  }
}

// 흉내 API (노트 3.3 표). 표 밖의 이름은 없다 → 실행 전 검사에서 미지원
RodiRunner.prototype.api = function (it, global) {
  var self = this;
  function def(name, fn) { it.setProperty(global, name, it.createNativeFunction(fn)); }
  function fail(text) { it.throwException(it.ERROR, text); }   // 스크립트의 try/catch가 잡을 수 있다
  function pin(name, n) {
    if (n !== Math.floor(n) || n < 0 || n >= RODI_PINS) fail(name + ': 번호는 0~' + (RODI_PINS - 1));
    return n;
  }
  def('getGeneralDigitalInput', function (n) { return self.di[pin('getGeneralDigitalInput', n)]; });
  def('getGeneralDigitalOutput', function (n) { return self.do[pin('getGeneralDigitalOutput', n)]; });
  def('setGeneralDigitalOutput', function (n, value) {
    pin('setGeneralDigitalOutput', n);
    if (value !== 0 && value !== 1) fail('setGeneralDigitalOutput: 값은 0 또는 1');
    self.setDo(n, value);
  });
  def('sleep', function (ms) { self.sleep('sleep', ms, fail); });
  def('_waitMs', function (ms) { self.sleep('wait', ms, fail); });
  def('_waitTick', function () {
    var t = self.now;
    self.cur.until = function () { return self.now > t; };
  });
  def('moveJoint', function (joint, v, a) {
    if (arguments.length > 3) fail('moveJoint: opts·콜백은 미지원. (joint, 속도, 가속)만 흉내 낸다');
    var target = it.pseudoToNative(joint);
    if (!Array.isArray(target) || target.length !== 6 ||
        !target.every(function (x) { return typeof x === 'number' && isFinite(x); })) {
      fail('moveJoint: 관절은 숫자 6개 배열 (deg)');
    }
    v = v === undefined ? 50 : v;     // 기본값 50 deg/s, 100 deg/s² (R 522)
    a = a === undefined ? 100 : a;
    if (!(v > 0 && a > 0)) fail('moveJoint: 속도와 가속은 0보다 커야 한다');
    if (self.motion) fail('moveJoint: 다른 흐름이 이동 중 (동시 이동은 흉내 내지 않는다)');
    if (self.cur !== self.flows[0]) self.warn('스레드의 moveJoint는 실물 미확인 (H p184~185)');
    var m = self.motion = new RodiMotion(self.joints, target, v, a);
    self.cur.until = function () { return m.done; };
  });
  def('moveStop', function (t) {
    if (self.cur !== self.flows[0]) self.warn('스레드의 moveStop은 실물 미확인 (H p184~185)');
    var m = self.motion;
    if (!m) return;                                         // 멈출 이동이 없다
    m.stop(t >= 0.01 ? Math.min(t, 0.5) : RODI_STOP_S);     // 0~0.5초, 0.01 미만은 자동 (R 1247)
    self.cur.until = function () { return m.done; };         // 멈출 때까지 기다린다
  });
  def('getCurrentJoint', function () { return it.nativeToPseudo(self.joints.slice()); });
  def('Thread', function (fn) {
    if (!(fn instanceof Interpreter.Object) || fn.class !== 'Function') fail('Thread: 함수가 필요하다');
    // fn()을 부르는 상태로 새 스택을 만든다 (interpreter.js의 setTimeout 작업과 같은 방식)
    var program = it.newNode();
    program.type = 'Program';
    program.body = [];
    var call = it.newNode();
    call.type = 'CallExpression';
    call.arguments = [];
    var bottom = new Interpreter.State(program, it.globalScope);
    bottom.done = false;
    var top = new Interpreter.State(call, it.globalScope);
    top.doneCallee_ = 2;
    top.func_ = fn;
    top.funcThis_ = it.globalObject;
    top.doneArgs_ = true;
    top.arguments_ = [];
    var id = self.flows.length;
    self.flows.push({name: '스레드' + id, stack: [bottom, top], until: null, done: false});
    self.say('상태', '스레드' + id + ' 시작');
    return id;
  });
  // 인터프리터에 딸린 setTimeout 무리는 표 밖이다 (실제 시계로 돈다)
  ['setTimeout', 'setInterval', 'clearTimeout', 'clearInterval'].forEach(function (n) {
    delete global.properties[n];
  });
};

RodiRunner.prototype.step = function (di, dtMs) {
  for (var i = 0; i < RODI_PINS; i++) this.di[i] = di[i] ? 1 : 0;
  if (this.done) return this.do.slice();
  this.now += dtMs;
  if (this.motion) {
    this.motion.advance(dtMs);
    this.joints = this.motion.joints();
    if (this.motion.done) this.motion = null;
  }
  for (i = 0; i < this.flows.length && !this.done; i++) {
    var f = this.flows[i];
    if (f.done || (f.until && !f.until())) continue;
    f.until = null;
    this.cur = f;
    this.interp.setStateStack(f.stack);
    try {
      for (var n = 0; n < RODI_STEP_LIMIT && !f.until; n++) {
        if (!this.interp.step()) {
          f.done = true;
          break;
        }
      }
    } catch (e) {
      this.end('오류', f.name + ' ' + rodiErrorText(e));
    }
    if (i === 0 && f.done && !this.done) this.end('상태', '스크립트 끝');   // 메인이 끝나면 스레드도 끝 (R 447)
  }
  return this.do.slice();
};

// 모든 흐름을 멈추고 출력을 끈다: 프로그램이 안 돌면 출력 LOW (H p63)
RodiRunner.prototype.end = function (kind, text) {
  this.say(kind, text);
  this.done = true;
  this.motion = null;
  for (var n = 0; n < RODI_PINS; n++) this.setDo(n, 0);
};

function rodiErrorText(e) {
  var at = /script:(\d+):/.exec(String(e && e.stack).split('\n').slice(1).join('\n'));
  var text = e && e.message !== undefined ? e.name + ': ' + e.message : String(e);
  return (at ? '(줄 ' + at[1] + ') ' : '') + text;
}

RodiRunner.prototype.sleep = function (name, ms, fail) {
  if (typeof ms !== 'number' || !(ms >= 0)) fail(name + ': 시간은 0 이상의 숫자 (ms)');
  if (ms < 10) this.warn(name + ' 인자가 작음: 초가 아니라 ms일 수 있음 (줄 ' + this.line() + ')');
  var self = this, end = this.now + ms;
  this.cur.until = function () { return self.now >= end; };
};

// 지금 흐름이 실행 중인 학생 스크립트 줄 (wait 같은 내장 함수 안이면 그것을 부른 줄)
RodiRunner.prototype.line = function () {
  var stack = this.cur.stack;
  for (var i = stack.length - 1; i >= 0; i--) {
    var loc = stack[i].node.loc;
    if (loc && loc.source === 'script') return loc.start.line;
  }
  return '?';
};

// 화면 상태 (노트 3.4): 감속 중이거나 DO1(비정상 정지)이면 정지 중, 이동 중이거나 DO0(동작 중)이면 동작 중
RodiRunner.prototype.state = function () {
  if ((this.motion && this.motion.brake) || this.do[1]) return '정지 중';
  if (this.motion || this.do[0]) return '동작 중';
  return '쉼';
};

RodiRunner.prototype.setDo = function (n, value) {
  if (this.do[n] === value) return;
  this.do[n] = value;
  this.events.push(['DO' + n, value]);
};

RodiRunner.prototype.say = function (kind, text) { this.log.push([kind, text]); };

RodiRunner.prototype.warn = function (text) {   // 같은 경고는 한 번만
  if (this.warned[text]) return;
  this.warned[text] = true;
  this.say('경고', text);
};

// 스레드가 스크립트의 전역 변수를 읽거나 쓰면 이름마다 한 번 경고한다 (노트 3.3 스레드 규칙).
// 실물 스레드가 전역 변수를 보는지는 문서에 없다(spec 8.1). 그래서 읽기만 하는 상수도 경고한다.
// ponytail: 전역만 본다. 함수 안에서 만든 스레드가 그 함수의 지역 변수를 함께 쓰는 것은 못 잡는다
RodiRunner.prototype.touch = function (name) {
  if (this.cur === this.flows[0] || this.builtin[name] || !this.isGlobal(name)) return;
  this.warn('변수 공유는 실물 미확인: ' + name + ' (스레드에서 읽거나 씀)');
};

RodiRunner.prototype.isGlobal = function (name) {
  var scope = this.interp.getScope();
  for (; scope && scope !== this.interp.globalScope; scope = scope.parentScope) {
    if (name in scope.object.properties) return false;
  }
  return true;
};

/* 관절 이동 하나. 가장 많이 움직이는 축이 사다리꼴 속도(최고 v deg/s, 가속 a deg/s²)로 가고
 * 나머지 축은 같은 비율로 따라간다. 짧은 이동은 최고 속도에 못 닿는다(삼각형). */
function RodiMotion(from, to, v, a) {
  var d = 0;
  for (var i = 0; i < 6; i++) d = Math.max(d, Math.abs(to[i] - from[i]));
  var ta = v / a;                                  // 가속 시간
  if (d < v * ta) {
    ta = Math.sqrt(d / a);
    v = a * ta;
  }
  this.from = from.slice();
  this.to = to.slice();
  this.d = d;
  this.v = v;
  this.a = a;
  this.ta = ta;
  this.end = d > 0 ? d / v + ta : 0;             // 끝나는 시각 (초)
  this.ms = 0;                                     // 지난 시간 (정수 ms로 쌓아 오차를 줄인다)
  this.done = false;
  this.brake = null;
}

RodiMotion.prototype.advance = function (dtMs) {
  this.ms += dtMs;
  if (this.ms / 1000 >= this.end) this.done = true;
};

// 시각 t초에 간 거리 (가장 많이 움직이는 축, deg)
RodiMotion.prototype.pos = function (t) {
  t = Math.min(t, this.end);
  var b = this.brake;
  if (b) {
    var s = t - b.t;
    return b.p + b.v * s - 0.5 * (b.v / b.dur) * s * s;
  }
  var a = this.a, ta = this.ta, end = this.end;
  if (t < ta) return 0.5 * a * t * t;
  if (t > end - ta) return this.d - 0.5 * a * (end - t) * (end - t);
  return 0.5 * a * ta * ta + this.v * (t - ta);
};

RodiMotion.prototype.speed = function (t) {
  if (t < this.ta) return this.a * t;
  if (t > this.end - this.ta) return this.a * (this.end - t);
  return this.v;
};

// 지금 속도에서 dur초 동안 0까지 줄인다. 남은 시간이 dur보다 짧으면 그대로 끝까지 간다
RodiMotion.prototype.stop = function (dur) {
  var t = this.ms / 1000;
  if (this.brake || this.end - t <= dur) return;
  this.brake = {t: t, p: this.pos(t), v: this.speed(t), dur: dur};
  this.end = t + dur;
};

RodiMotion.prototype.joints = function () {
  if (this.done && !this.brake) return this.to.slice();
  var k = this.d > 0 ? this.pos(this.ms / 1000) / this.d : 1;
  var from = this.from, to = this.to;
  return from.map(function (f, i) { return f + (to[i] - f) * k; });
};
