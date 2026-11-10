# 교육 사본 변경

- 통신부, 상태 해독, 래더, 로봇 스크립트는 원본 그대로다.
- app.py와 web/ui.js의 HTTP 미응답 문구는 재누름 대신 새 상태·로그 확인을 안내하도록 바꿨다. 통신 로직 변경은 없다.
- web/index.html은 Pretendard를 먼저 쓰고 Malgun Gothic을 대체 글꼴로 둔다. 레이아웃 변경은 없다.
- web/index.html에 hidden 표시 규칙을 명시해 model에서 포트 찾기 구역이 나타나는 기존 CSS 충돌을 해소했다.
- 강사 사본은 `Sequencer._why_off`에 D 정책을 추가했다. `auto_maybe_on`이면 개별 이동1·이동2·그리퍼 버튼을 보류한다. 원본은 이 세 버튼을 허용한다.
- 강사 회귀 시험의 해당 버튼 집합 기대값 한 줄도 D 교육 정책에 맞췄다. 나머지 원본 시험은 유지했다.
- 학생 사본은 A `_accept`, B `_cycle`을 명시적 미구현으로 두었다. C `_until`은 정상 대기를 제공하고 시간 초과 처리만 미구현이다. D는 TODO이며 초기에는 원본 버튼 정책이다.
- `lesson_ui.py`는 model 전용 실행 입구이고 `lesson_scenarios.py`의 고장은 Python 교육 모형에만 적용한다.
- `check_student.py`는 실제 학생 함수를 호출하는 동작 검사다. 다른 미완성 과제가 해당 단계 검사를 막지 않도록 시험 대역을 사용한다.
