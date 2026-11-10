# 수업 코드 출처

고정 커밋: `0a5c603ff9ed432a47e92958a745931dc7c1225f`

`git archive`로 만든 사본이다. 외부 저장소의 현재 수정 내용은 포함하지 않았다.
단독 교육 모형은 `--target model`로 실행한다. 모형 성공은 실물 성공을 뜻하지 않는다.
가짜 장치 연결은 fake_plc, fake_robot와 `python -m picknplace_full --target tcp:127.0.0.1:5020`을 함께 실행한다. 학생 자기 sequence.py가 이 PC 프로그램에서 실행된다.
lesson_ui.py는 model 전용이다. 가짜 장치 TCP 연결에는 사용하지 않는다.
fake_robot/vendor에는 오프라인 자원과 각 라이선스를 함께 포함했다.
로봇 DO0은 요청 해제까지 유지하는 최신 규칙을 반영했다. 티칭 좌표·설정·실물 동작은 별도 확인하며 바로 업로드하지 않는다.
통신부·래더·로봇 스크립트는 고정 커밋 그대로이며 교육 변경은 `PATCHES.md`를 따른다.
