# Room 사용자 목록 서버 계약

## 닉네임 등록

WebSocket 연결 후 Room 생성 또는 입장 전에 닉네임을 한 번 등록한다.

```json
{ "type": "set_nickname", "nickname": "WATA" }
```

성공 응답:

```json
{ "type": "nickname_confirmed", "nickname": "WATA" }
```

닉네임은 NFKC 정규화와 앞뒤 공백 제거 후 1~20자여야 한다. 제어문자와 줄 구분 문자는 허용하지 않는다. 닉네임 중복은 허용하며 서버는 내부 `connection_id`로 사용자를 구분한다. 한 WebSocket 연결에서는 등록 후 닉네임을 변경할 수 없다.

닉네임 없이 `create_room` 또는 `join_room`을 전송하면 `NICKNAME_REQUIRED`, 잘못된 닉네임이나 재등록은 `INVALID_NICKNAME` 오류를 반환한다.

`connected`는 다음 capability를 포함한다.

```json
{
  "nickname_required": true,
  "nickname_min_length": 1,
  "nickname_max_length": 20
}
```

## Room 사용자 목록

기존 count와 색상 필드를 유지하고 `player_list`, `observer_list`를 추가한다.

```json
{
  "type": "room_members",
  "players": ["BLACK", "WHITE"],
  "ready_colors": ["BLACK"],
  "player_count": 2,
  "max_players": 2,
  "observer_count": 2,
  "member_count": 4,
  "max_members": 99,
  "player_list": [
    { "nickname": "WATA", "color": "BLACK", "ready": true },
    { "nickname": "WATA", "color": "WHITE", "ready": false }
  ],
  "observer_list": [
    { "nickname": "Guest" },
    { "nickname": "Guest" }
  ]
}
```

- Player는 현재 색상 순서로 전달한다.
- Observer는 Room 입장 순서로 전달한다.
- 동일 닉네임 항목을 병합하면 안 된다.
- 내부 `connection_id`는 전송하지 않는다.
- 입장, 퇴장, 역할 변경 및 Ready 변경 때 Room 전체에 최신 snapshot을 방송한다.
