# Ready 서버 계약

## 시작 조건

Room 입장자는 모두 Observer다. 두 Player가 역할 전환으로 확정된 상태에서 양쪽이
`ready`를 보내야 최초 대국 또는 다음 대국을 시작한다.

## Client → Server

```json
{ "type": "ready" }
```

`WAITING` 또는 `FINISHED` 상태에서만 사용할 수 있다. 같은 플레이어의 중복 요청은
멱등적으로 처리하며 Ready 인원을 증가시키지 않는다.

## Server → Client

Ready 요청자에게는 `ready_confirmed`를 보내고 상대 Player에게만 다음 메시지를 전송한다.

```json
{
  "type": "player_ready",
  "color": "WHITE",
  "ready_count": 1,
  "required": 2
}
```

두 명이 모두 Ready이면 모든 Room Member에게 개별 `game_start`를 전송한다. Player는
`your_color`를 받고 Observer는 `your_color: null`을 받는다.

```json
{
  "type": "game_start",
  "game_type": "GOMOKU",
  "your_color": "WHITE",
  "board_size": 15,
  "win_length": 5,
  "starting_color": "WHITE",
  "current_turn": "WHITE"
}
```

전송 순서는 다음과 같다.

```text
player_ready
game_start
game_state
room_list
```

## 상태 초기화

플레이어가 퇴장하거나 연결이 종료되면 Room의 Ready 상태를 모두 제거하고 게임을
빈 `WAITING` 상태로 되돌린다. 게임 종료 시에도 다음 라운드용 Ready 상태는 비어 있다.

## 오류

| code | 의미 |
|---|---|
| `NOT_IN_ROOM` | Room에 입장하지 않음 |
| `READY_REQUIRES_TWO_PLAYERS` | 두 플레이어가 모두 연결되지 않음 |
| `READY_NOT_AVAILABLE` | 이미 게임이 진행 중임 |
| `UNDO_PENDING` | Undo 요청을 처리 중임 |

## Restart 대체

Ready가 최초 시작과 다음 라운드 시작을 모두 담당한다. 기존 `restart_request`,
`restart_requested`, `restart` 메시지는 더 이상 지원하지 않는다.
