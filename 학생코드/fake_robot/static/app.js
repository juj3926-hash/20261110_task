// 가짜 로봇 3D 화면. 서버(/api/state)를 0.1초마다 읽어 팔·표시등·상태·로그를 그린다.
// 학생 스크립트는 worker.js(Web Worker)가 돌리고, Worker가 서버와 직접 I/O를 주고받는다.
import * as THREE from 'three';
import { OrbitControls } from '/vendor/OrbitControls.js';

const $ = (id) => document.getElementById(id);
const POLL_MS = 100;
let state = null;        // 마지막 /api/state
let logSeen = 0;         // 이미 그린 로그 번호
let worker = null;
let source = null;       // 마지막으로 연 스크립트
const shown = [0, 0, 90, 0, 90, 0];   // 화면의 관절 (deg). 서버 값을 부드럽게 따라간다

// ---- 3D 팔: HCR-3A 비슷한 6축. 치수는 근사(m). 역기구학 없이 관절 각도를 그대로 돌린다 ----
// WebGL이 없는 PC에서는 렌더러가 예외를 낸다. 3D만 빼고 표시등·로그·파일 열기는 그대로 쓴다.
try {
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0xeef1f5);
  const camera = new THREE.PerspectiveCamera(40, 1, 0.01, 20);
  camera.position.set(0.95, 0.75, 1.05);
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  $('view').prepend(renderer.domElement);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.target.set(0, 0.3, 0);
  controls.update();
  scene.add(new THREE.HemisphereLight(0xffffff, 0x8a8f99, 2.2));
  const sun = new THREE.DirectionalLight(0xffffff, 1.6);
  sun.position.set(1, 2, 1.5);
  scene.add(sun);
  scene.add(new THREE.GridHelper(1.2, 12, 0x98a2b3, 0xd0d5dd));   // 칸 0.1 m

  const white = new THREE.MeshStandardMaterial({ color: 0xf5f6f8, roughness: 0.5 });
  const dark = new THREE.MeshStandardMaterial({ color: 0x3b4252, roughness: 0.6 });
  const blue = new THREE.MeshStandardMaterial({ color: 0x2e90fa, roughness: 0.5 });

  // 원기둥 하나. axis = 'x' | 'y' | 'z' 방향, 가운데가 (x, y, z)
  function cylinder(parent, radius, length, axis, material, x = 0, y = 0, z = 0) {
    const mesh = new THREE.Mesh(new THREE.CylinderGeometry(radius, radius, length, 32), material);
    if (axis === 'x') mesh.rotation.z = Math.PI / 2;
    if (axis === 'z') mesh.rotation.x = Math.PI / 2;
    mesh.position.set(x, y, z);
    parent.add(mesh);
  }

  // 관절 = 부모 안 (x, y, z)에서 axis로 도는 묶음
  function joint(parent, axis, x, y, z) {
    const group = new THREE.Group();
    group.position.set(x, y, z);
    group.userData.axis = axis;
    parent.add(group);
    return group;
  }

  const J = [];
  cylinder(scene, 0.075, 0.10, 'y', dark, 0, 0.05, 0);              // 바닥 받침
  J[0] = joint(scene, 'y', 0, 0.10, 0);                              // J1 바닥 회전
  cylinder(J[0], 0.06, 0.12, 'y', white, 0, 0.06, 0);
  J[1] = joint(J[0], 'z', 0, 0.12, 0);                               // J2 어깨 (바닥에서 0.22 m)
  cylinder(J[1], 0.066, 0.13, 'z', dark);
  cylinder(J[1], 0.045, 0.27, 'y', white, 0, 0.135, 0);              // 위팔 0.27 m
  J[2] = joint(J[1], 'z', 0, 0.27, 0);                               // J3 팔꿈치
  cylinder(J[2], 0.055, 0.11, 'z', dark);
  cylinder(J[2], 0.038, 0.23, 'y', white, 0, 0.115, 0);              // 아래팔 0.23 m
  J[3] = joint(J[2], 'z', 0, 0.23, 0);                               // J4 손목1
  cylinder(J[3], 0.042, 0.09, 'z', dark);
  cylinder(J[3], 0.034, 0.08, 'y', white, 0, 0.04, 0);
  J[4] = joint(J[3], 'y', 0, 0.08, 0);                               // J5 손목2
  cylinder(J[4], 0.042, 0.07, 'y', dark);
  J[5] = joint(J[4], 'x', 0.06, 0.02, 0);                            // J6 손목3 (플랜지)
  cylinder(J[5], 0.036, 0.04, 'x', dark);
  cylinder(J[5], 0.034, 0.012, 'x', blue, 0.026, 0, 0);
  for (const y of [-0.018, 0.018]) {                                 // 그리퍼 자리 (돌아가는 것이 보이게)
    const finger = new THREE.Mesh(new THREE.BoxGeometry(0.04, 0.012, 0.012), blue);
    finger.position.set(0.05, y, 0);
    J[5].add(finger);
  }

  function resize() {
    const view = $('view');
    renderer.setSize(view.clientWidth, view.clientHeight);
    camera.aspect = view.clientWidth / Math.max(1, view.clientHeight);
    camera.updateProjectionMatrix();
  }
  window.addEventListener('resize', resize);
  resize();

  let last = performance.now();
  function frame(now) {
    const k = Math.min(1, ((now - last) / 1000) * 12);
    last = now;
    if (state) state.joints.forEach((q, i) => { shown[i] += (q - shown[i]) * k; });
    J.forEach((g, i) => {
      const sign = g.userData.axis === 'z' ? -1 : 1;                 // + 각도가 화면 오른쪽(+x)으로 굽게
      g.rotation[g.userData.axis] = sign * THREE.MathUtils.degToRad(shown[i]);
    });
    $('joints').textContent = '관절 (deg)  ' + shown.map((q, i) => `J${i + 1} ${q.toFixed(1)}`).join('  ');
    renderer.render(scene, camera);
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
} catch (e) {
  console.error(e);
  $('note').textContent = '3D 화면을 쓸 수 없음 (WebGL 없음)';
}

// ---- 서버 ----
async function poll() {
  try {
    const r = await fetch(`/api/state?log=${logSeen}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    state = await r.json();
    show(state);
  } catch (e) {
    $('plc').textContent = `서버 응답 없음 (${e.message})`;
    $('plc').className = 'off';
  }
  setTimeout(poll, POLL_MS);
}

async function post(path, body) {
  try {
    const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                  body: JSON.stringify(body) });
    const answer = await r.json();
    if (!r.ok) localLog('오류', answer.error);
    return r.ok;
  } catch (e) {
    localLog('오류', `서버에 보내지 못함: ${e.message}`);
    return false;
  }
}

function show(s) {
  $('plc').textContent = s.connected ? `PLC 연결됨 (${s.plc})` : `PLC 연결 없음 (${s.plc}에 1초마다 다시 접속)`;
  $('plc').className = s.connected ? 'on' : 'off';
  $('state').textContent = s.state;
  s.di.forEach((v, i) => lamp(`DI${i}`, v));
  s.do.forEach((v, i) => lamp(`DO${i}`, v));
  document.querySelector(`input[name=mode][value=${s.mode}]`).checked = true;
  fillJoints('home', s.home);
  fillJoints('pick', s.pick);
  if (s.last < logSeen) logSeen = 0;   // 서버를 다시 켰다. 번호가 1부터 다시 가므로 다음 읽기에 처음부터 받는다
  for (const e of s.log) addLog(e.t, e.kind, e.text);
  if (s.log.length) logSeen = s.log[s.log.length - 1].n;
}

function lamp(id, on) {
  $(id).classList.toggle('on', Boolean(on));
  $(id).title = on ? '켜짐' : '꺼짐';
}

function addLog(time, kind, text) {
  const line = document.createElement('div');
  line.className = `k-${kind}`;
  line.textContent = `${time} [${kind}] ${text}`;
  $('log').prepend(line);
  while ($('log').childElementCount > 300) $('log').lastChild.remove();
}

function localLog(kind, text) {
  const d = new Date();
  addLog(`${d.toTimeString().slice(0, 8)}.${String(d.getMilliseconds()).padStart(3, '0')}`, kind, text);
}

// ---- HOME·PICK 편집: 고치기 시작한 줄은 [적용]이 될 때까지 서버 값으로 덮지 않는다 ----
const editors = { home: [], pick: [] };
const dirty = { home: false, pick: false };
for (const name of ['home', 'pick']) {
  for (let i = 0; i < 6; i++) {
    const input = document.createElement('input');
    Object.assign(input, { type: 'number', step: '1', min: '-360', max: '360' });
    input.setAttribute('aria-label', `${name.toUpperCase()} J${i + 1}`);
    input.addEventListener('input', () => { dirty[name] = true; });
    $(name).append(input);
    editors[name].push(input);
  }
}

function fillJoints(name, values) {
  if (!dirty[name]) editors[name].forEach((input, i) => { input.value = Number(values[i].toFixed(2)); });
}

$('apply').onclick = async () => {
  const body = { home: editors.home.map((el) => Number(el.value)), pick: editors.pick.map((el) => Number(el.value)) };
  if (await post('/api/joints', body)) dirty.home = dirty.pick = false;
};

// ---- 학생 스크립트: 파일을 열면 학생 스크립트 모드로 바꾸고 Worker에서 돌린다 ----
function startWorker() {
  stopWorker();
  worker = new Worker('/worker.js');
  worker.onmessage = (e) => { if (e.data.type === 'ended') stopWorker(); };
  worker.onerror = (e) => {
    localLog('오류', `Worker 오류: ${e.message}`);
    stopWorker();
  };
  worker.postMessage({ type: 'start', source, joints: state ? state.joints : shown });
  buttons();
}

function stopWorker() {
  if (worker) worker.terminate();
  worker = null;
  buttons();
}

function buttons() {
  $('rerun').disabled = source === null;
  $('stop').disabled = !worker;
}

$('file').onchange = async () => {
  const file = $('file').files[0];
  if (!file) return;
  source = await file.text();
  $('file').value = '';   // 비운다. 고친 같은 파일을 다시 골라도 change가 나서 새로 읽는다
  $('script-name').textContent = file.name;
  if (await post('/api/mode', { mode: 'script' })) startWorker();
};
$('rerun').onclick = async () => {
  if (source !== null && await post('/api/mode', { mode: 'script' })) startWorker();
};
$('stop').onclick = () => { if (worker) worker.postMessage({ type: 'stop' }); };
for (const radio of document.querySelectorAll('input[name=mode]')) {
  radio.onchange = async () => {
    if (radio.value === 'builtin') stopWorker();
    await post('/api/mode', { mode: radio.value });
  };
}

poll();
