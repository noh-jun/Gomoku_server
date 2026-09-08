# 오목 착수 되돌리기 서버 계약

## 범위

착수 되돌리기는 진행 중인 2인 오목 Room에서만 지원한다. 오셀로와 종료된 대국에서는
지원하지 않는다. 요청자의 상대만 수락하거나 거절할 수 있다.

서버가 제거할 수와 복원할 턴을 결정한다. 클라이언트는 이를 계산하지 않고
`undo_result` 직후의 authoritative `game_state`를 최종 상태로 사용한다.

## 제거 규칙

| 요청 시점 | 제거할 수 | 수락 후 턴 |
|---|---:|---|
| 요청자가 방금 둬 상대 차례 | 요청자의 최신 1수 | 요청자 |
| 상대가 둬 요청자 차례 | 상대 최신 수와 요청자의 직전 수, 총 2수 | 요청자 |

요청자의 이전 착수가 최근 이력에 없으면 `UNDO_NOT_AVAILABLE`이다.

## Client → Server

```json
{ "type": "undo_request" }
```

```json
{ "type": "undo_response", "accepted": true }
```

`accepted`는 JSON boolean만 허용한다. 응답자가 아닌 연결의 응답은
`NOT_UNDO_RESPONDER`다.

## Server → Client

요청이 접수되면 Room의 두 플레이어에게 전송한다.

```json
{
  "type": "undo_requested",
  "game_type": "GOMOKU",
  "requester_color": "WHITE",
  "undo_count": 2
}
```

수락 또는 거절 결과도 두 플레이어에게 전송한다.

```json
{
  "type": "undo_result",
  "game_type": "GOMOKU",
  "accepted": true,
  "requester_color": "WHITE",
  "undone": [
    {"x": 8, "y": 8, "color": "BLACK"},
    {"x": 7, "y": 7, "color": "WHITE"}
  ],
  "current_turn": "WHITE"
}
```

`undone`은 제거된 순서, 즉 최신 수부터 과거 수 순서다. 수락된 경우 전송 순서는 다음과
같다.

```text
undo_result
game_state
```

거절이면 `undone`은 빈 배열이고 보드와 턴은 변하지 않으며 별도 `game_state`는 보내지
않는다.

## Pending 상태

요청 접수부터 응답까지 Room은 착수와 Ready를 일시 정지한다. 이때 해당 요청은
`UNDO_PENDING`으로 거부한다. 중복 되돌리기 요청은 `UNDO_ALREADY_PENDING`이다.

다음 상태 전환은 pending 요청을 제거한다.

- 수락 또는 거절
- 플레이어 퇴장/disconnect
- 새 라운드 시작
- Room 초기화

## 오류 코드

| code | 의미 |
|---|---|
| `UNDO_NOT_AVAILABLE` | 되돌릴 요청자 착수가 없거나 대국이 진행 중이 아님 |
| `UNDO_ALREADY_PENDING` | 이미 응답 대기 중인 요청이 있음 |
| `UNDO_PENDING` | 응답 대기 중 착수 또는 Ready 시도 |
| `NOT_UNDO_RESPONDER` | 요청자의 상대가 아닌 연결이 응답 |
| `UNSUPPORTED_GAME_OPERATION` | 오셀로 등 지원하지 않는 게임에서 요청 |

Lobby에서 요청하면 기존과 같이 `NOT_IN_ROOM`이다.

## 서버 상태 복원

`GomokuGame`은 전체 라운드의 성공한 착수 이력을 보관한다. 금수처럼 거부된 착수는
기록하지 않는다. 수락 시 서버는 하나의 Room lock 안에서 다음을 함께 복원한다.

- board
- move history와 move count
- last move
- current turn
- winner, loser, game-over reason
- Renju forbidden moves

현재 계약은 `PLAYING`에서만 요청을 받으므로 이미 승패가 확정된 마지막 수는 되돌릴 수
없다.
