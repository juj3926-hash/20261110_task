/* PICKNPLACE_FULL 화면. 0.25초마다 /api/status를 묻는다. 버튼의 켜짐·라벨·이유는 서버 값을 그대로 쓴다. */
const el = id => document.getElementById(id);
const buttons = document.querySelectorAll("[data-button]");

function put(node, text) {  // 바뀔 때만 쓴다. 로그 창 스크롤과 화면 읽기 프로그램을 흔들지 않는다
    if (node.textContent !== text) node.textContent = text;
}

function show(s) {
    const st = s.status;
    const raw = st ? String(st.raw).padStart(4, "0") : "----";
    put(el("target"), "연결 대상 " + s.target);
    put(el("state"), st ? st.text + " (" + raw + ")" : "상태 없음");
    put(el("detail"), st ? "원시 코드 " + raw + " · 우편함 " + st.mailbox + " · 마지막 읽은 시각 " + st.at : "");
    put(el("comm"), s.comm_ok ? "" : "통신 실패 (마지막 정상 " + (st ? st.at : "없음") + ")");
    for (const b of buttons) {
        const state = s.buttons[b.dataset.button];
        put(b, state.label);
        b.disabled = !state.enabled;
        put(b.nextElementSibling, state.hint);
    }
    el("code-send").disabled = !s.buttons.code.enabled;
    put(el("code-hint"), s.buttons.code.hint);
    const q = s.sequence;
    put(el("step"), q.running ? q.step || "시작하는 중" : "자동 순서 중 아님");
    put(el("sequence"), [
        q.stop_requested ? "정지 요청함" : "",
        q.auto_maybe_on ? "PLC 자동 운전이 켜져 있을 수 있음" : "",
        q.last_result ? "마지막 결과: " + q.last_result : "",
    ].filter(Boolean).join(" · "));
    el("ports-section").hidden = !s.target.startsWith("auto");
    put(el("ports"), s.ports.map(p => p[0] + "  " + p[1]).join("\n") || "찾은 포트 없음");
    put(el("log"), s.events.map(e => e.at + " [" + e.tag + "] " + e.text).reverse().join("\n"));
}

function say(text) {  // 응답 줄. 받은 시각을 붙여 같은 응답이 다시 와도 보이게 한다
    const t = new Date();
    put(el("message"), t.toTimeString().slice(0, 8) + "." + String(t.getMilliseconds()).padStart(3, "0") + " " + text);
}

async function post(path, payload) {
    try {
        const r = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json"},
                                     body: JSON.stringify(payload)});
        say((await r.json()).message);
    } catch (e) {
        say("응답 미확인 (" + e.message + "). 처리 여부가 미확인입니다. 새 상태와 로그를 확인하세요");
    }
}

for (const b of buttons) b.onclick = () => post("/api/press", {button: b.dataset.button});
el("code-form").onsubmit = e => {
    e.preventDefault();
    post("/api/code", {code: Number(el("code").value)});
};
el("rescan").onclick = () => {
    say("포트를 다시 찾는 중입니다");
    post("/api/rescan", {});
};

async function poll() {
    try {
        const r = await fetch("/api/status");
        if (!r.ok) throw new Error("HTTP " + r.status);
        show(await r.json());
    } catch (e) {
        put(el("comm"), "서버 응답 없음 (" + e.message + "). 프로그램이 실행 중인지 확인하세요");
    } finally {
        setTimeout(poll, 250);
    }
}
poll();
