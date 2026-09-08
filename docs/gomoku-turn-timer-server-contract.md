# 게임별 Turn Timer 서버 계약

## 적용 범위

Turn Timer는 `GOMOKU`와 `OTHELLO`에 서로 다른 규칙으로 적용한다.

- `GOMOKU`: 시간 초과 시 돌을 놓지 않고 상대에게 턴을 넘긴다.
- `OTHELLO`: 시간 초과 시 서버가 현재 합법 수 중 하나를 균등 무작위로 선택해 자동 착수한다.

## 방 생성

클라이언트는 `create_room`에 다음 필드를 추가한다.

```json
{
  "type": "create_room",
  "room_name": "5초 오목",
  "game_type": "GOMOKU",
  "turn_time_limit_sec": 5
}
```

`turn_time_limit_sec`은 `5`, `10`, `15`, `30`, `60`, `null` 중 하나다. `null`은 무제한이며 필드 생략도 `null`과 같다. 그 외 값은 `INVALID_TURN_TIME_LIMIT` 오류다.

서버는 연결 직후 게임별 생성 옵션과 timeout 동작을 광고한다.

```json
{
  "room_creation_options": {
    "GOMOKU": {
      "turn_time_limits": [null, 5, 10, 15, 30, 60],
      "timeout_action": "SKIP_TURN"
    },
    "OTHELLO": {
      "turn_time_limits": [null, 5, 10, 15, 30, 60],
      "timeout_action": "RANDOM_LEGAL_MOVE"
    }
  }
}
```

선택값은 `room_list.rooms[]`, `joined`, `game_start`, `game_state`의 `turn_time_limit_sec`으로 전달된다.

## 진행 상태

`game_state`에는 다음 필드가 추가된다.

```json
{
  "turn_time_limit_sec": 10,
  "turn_remaining_ms": 10000,
  "turn_revision": 7
}
```

- `turn_remaining_ms`: 서버 monotonic clock으로 계산한 현재 턴의 남은 밀리초. 무제한, Ready 대기, Undo 응답 대기, 종료 상태에서는 `null`이다. 클라이언트 PC의 Unix 시각과 빼서 계산하지 않고, 수신한 duration을 로컬 monotonic clock으로 감소시킨다.
- `turn_revision`: 타이머가 시작, 중단, 재시작될 때 증가한다. 클라이언트는 더 작은 revision의 이전 상태를 폐기한다.
- 화면 카운트다운은 안내용이다. 실제 만료 판정은 서버가 수행한다.

## 오목 시간 만료

제한 시간 내 착수하지 않으면 돌, `move_history`, `last_move`를 변경하지 않고 턴만 상대에게 넘긴다. 서버는 방의 모든 Player와 Observer에게 다음 순서로 방송한다.

```json
{
  "type": "turn_timeout",
  "game_type": "GOMOKU",
  "timed_out_color": "BLACK",
  "current_turn": "WHITE"
}
```

그 다음 새 남은 시간을 포함한 `game_state`를 방송한다. 마감과 동시에 도착한 착수도 서버의 room lock 안에서 판정하며, 이미 만료됐다면 착수는 반영하지 않고 요청자에게 `TURN_EXPIRED` 오류를 보낸다.

## 오셀로 시간 만료

오셀로는 턴을 임의로 넘기지 않는다. 제한 시간이 지나면 서버가 현재 차례의
`legal_moves` 중 하나를 균등 무작위로 선택하고 정상 착수와 같은 게임 엔진 경로로
적용한다. 따라서 뒤집기, 강제 패스, 점수 및 게임 종료 판정도 일반 착수와 동일하다.

서버는 방의 모든 Player와 Observer에게 다음 순서로 방송한다.

1. `turn_timeout`
2. 자동 착수의 `move_result`
3. authoritative `game_state`
4. 종료된 경우 `game_over`

```json
{
  "type": "turn_timeout",
  "game_type": "OTHELLO",
  "timed_out_color": "BLACK",
  "current_turn": "WHITE",
  "action": "RANDOM_LEGAL_MOVE",
  "move": {"x": 2, "y": 3}
}
```

deadline 이후 도착한 사용자 착수는 반영하지 않는다. 해당 요청이 room lock 안에서
먼저 만료를 발견하더라도 자동 착수 결과를 위 순서로 방송한 뒤 요청자에게
`TURN_EXPIRED` 오류를 보낸다.

## 공통 Ready 및 오목 Undo 정책

- 두 Player가 Ready하여 게임이 시작될 때 첫 턴의 전체 시간이 시작된다.
- 정상 착수 후 상대 턴은 전체 시간으로 시작된다.
- Undo 요청 중에는 타이머를 일시정지한다.
- Undo 거절 시 요청 직전 남아 있던 시간으로 재개한다.
- Undo 수락 시 되돌린 뒤 요청자의 턴에 전체 시간을 새로 부여한다.
- 게임 종료 또는 Player 이탈로 대국이 중단되면 타이머를 제거한다.
