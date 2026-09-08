# 기권 서버 계약

- `PLAYING` 상태의 Player는 착수 여부와 관계없이 `{ "type": "resign" }`을 전송할 수 있다.
- Gomoku와 Othello 모두 지원한다.
- Observer, 대기·종료 상태, Undo 처리 중 요청은 거절한다.
- 요청자는 패자, 상대 Player는 승자가 되며 종료 사유는 `RESIGNATION`이다.
- 보드와 착수 기록은 유지하고 Turn Timer는 종료한다.
- 서버는 Room 전체에 `game_over` 후 authoritative `game_state`를 방송한다.
- Othello `game_over`에는 기권 시점의 점수를 포함한다.
- 연결 종료는 자동 기권으로 처리하지 않는다.
