# 저장소에 넣은 브라우저 라이브러리

오프라인으로 쓰려고 파일을 그대로 넣었다. 고치지 않는다. 바꿀 때는 이 표와 해시를 함께 고친다.
`.gitattributes`(`* -text`)로 줄바꿈 변환을 막아 바이트가 원본과 같다. 시험(`tests/test_fake_robot_page.py`)이 아래 SHA-256을 확인한다.

| 파일 | 이름 | 버전 | 출처 | 라이선스 | SHA-256 |
| --- | --- | --- | --- | --- | --- |
| `interpreter.js` | JS-Interpreter (Neil Fraser) | 커밋 `45d00b0c86e48cca1bb3af0f711bc4c0d626c359` (2026-06-12) | https://cdn.jsdelivr.net/gh/NeilFraser/JS-Interpreter@45d00b0c86e48cca1bb3af0f711bc4c0d626c359/interpreter.js | Apache-2.0, `LICENSE-JS-Interpreter.txt` | `b6f248bf75f834e33fa31512c3e7bf4be0bcaa6122624e4cda6c3346b70bc70f` |
| `acorn.js` | Acorn 0.5.0 (Marijn Haverbeke), JS-Interpreter가 고친 판 | 위와 같은 커밋 | https://cdn.jsdelivr.net/gh/NeilFraser/JS-Interpreter@45d00b0c86e48cca1bb3af0f711bc4c0d626c359/acorn.js | MIT, `LICENSE-acorn.txt` (npm `acorn@0.5.0`의 LICENSE) | `6146ec5e6d71caa6c5a6c117002278389db27cf5855609301b8d66d4cac46b96` |
| `three.module.min.js` | three.js | npm `three@0.171.0` (2024-11-29), `build/three.module.min.js` | https://registry.npmjs.org/three/-/three-0.171.0.tgz | MIT, `LICENSE-three.txt` (패키지의 LICENSE) | `7beca683cc871059d68974082de4a98e3e589ae0c4322f1a22a25954d97552f1` |
| `three.core.min.js` | three.js (위 파일이 `./three.core.min.js`로 불러옴) | 같은 패키지, `build/three.core.min.js` | 같은 tarball | MIT, `LICENSE-three.txt` | `b54705636e2190fa8b8ccaa7212407340b5b4248a86fda2709a5a15798880b3e` |
| `OrbitControls.js` | three.js 예제의 OrbitControls (마우스로 시점 돌리기) | 같은 패키지, `examples/jsm/controls/OrbitControls.js` | 같은 tarball | MIT, `LICENSE-three.txt` | `ffe21e98ec99e876f1865bfc990d2ad302464f6b400f3295571fa5fc41abddfc` |

- JS-Interpreter
  - 원본 저장소: https://github.com/NeilFraser/JS-Interpreter (태그가 없어 커밋으로 고정했다)
  - 받은 파일의 git blob SHA-1이 GitHub API의 그 커밋 파일과 같은지 확인했다: `interpreter.js` `b156581a069cb84f734a81940b8555ea814c77c5`, `acorn.js` `1d79a74e788c3cb5f3d884e1b1c4f5c313d4a5d8`
  - 쓰는 곳: `fake_robot/static/rodi.js`. Web Worker(`static/worker.js`)는 `importScripts`로, 시험(Node)은 `vm.runInThisContext`로 같은 순서(acorn → interpreter → rodi)로 불러온다.
- three.js
  - tarball이 레지스트리의 `dist.integrity`(`sha512-Y/lAXPaKZPcEdkKjh0JOAHVv8OOnv/NDJqm0wjfCzyQmfKxV7zvkwsnBgPBKTzJHToSOhRGQAGbPJObT59B/PQ==`)·`shasum`(`3c0dd3f8fa14e78a7f8db6e416b98f264f1185c0`)과 같은지 확인하고 세 파일만 꺼냈다.
  - 0.171.0을 고른 이유: 이 판에는 압축본(`*.min.js`, 두 파일 합쳐 약 0.7 MB)이 있다. 최신 0.186.1은 압축본이 없어 약 2.1 MB다.
  - 쓰는 곳: `fake_robot/static/app.js`. `index.html`의 import map이 `three`를 `/vendor/three.module.min.js`로 잇는다(OrbitControls가 `'three'`를 불러온다).
