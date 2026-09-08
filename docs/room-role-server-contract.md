# Room Player/Observer 서버 계약

## Room 구성

- Room 전체 Member는 최대 99명이다.
- Player는 최대 2명이고 나머지는 Observer다.
- 모든 사용자는 Room 입장 시 Observer다.
- 게임 진행 메시지는 Player와 Observer를 포함한 모든 Member에게 전송한다.

## 역할 전환

```json
{ "type": "become_player" }
```

```json
{ "type": "become_observer" }
```

게임이 `PLAYING`이면 양쪽 역할 전환을 모두 거절한다. Player가 Observer로 전환하면
Room의 Ready 상태를 초기화한다.

요청자에게는 다음 결과를 보낸다.

```json
{ "type": "role_changed", "your_role": "PLAYER", "your_color": "WHITE" }
```

Room 전체에는 `room_members`를 보낸다.

```json
{
  "type": "room_members",
  "players": ["WHITE", "BLACK"],
  "ready_colors": [],
  "player_count": 2,
  "max_players": 2,
  "observer_count": 3,
  "member_count": 5,
  "max_members": 99
}
```

## 입장과 게임 시작

`joined`는 항상 `your_role: "OBSERVER"`, `your_color: null`을 포함한다. 정확히 두
Player가 정해져야 Ready할 수 있으며 양쪽 Ready 후 게임을 시작한다.

Ready 요청자에게는 `ready_confirmed`를, 상대 Player에게만 `player_ready`를 보낸다.
Observer는 Ready 알림을 받지 않는다.

`game_start`는 모든 Member에게 개별 전송한다. Player는 자신의 `your_color`를 받고
Observer는 `your_role: "OBSERVER"`, `your_color: null`을 받는다.

## 전달 범위

| 메시지 | 대상 |
|---|---|
| `ready_confirmed` | Ready 요청자 |
| `player_ready` | 상대 Player |
| `undo_requested` | 두 Player |
| `move_result`, `undo_result`, `game_state`, `game_over` | Room 전체 Member |
| `room_members` | Room 전체 Member |

## 오류

| code | 의미 |
|---|---|
| `ROOM_FULL` | Room Member 99명 |
| `PLAYER_SLOTS_FULL` | Player 자리 2개가 모두 사용 중 |
| `PLAYER_REQUIRED` | Observer가 Player 전용 동작 요청 |
| `ALREADY_PLAYER` | Player가 Player 전환 요청 |
| `ALREADY_OBSERVER` | Observer가 Observer 전환 요청 |
| `ROLE_CHANGE_NOT_AVAILABLE` | 게임 진행 중 역할 전환 요청 |
| `READY_REQUIRES_TWO_PLAYERS` | Player가 정확히 2명이 아닌 상태에서 Ready |
