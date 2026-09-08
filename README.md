# 실시간 2인용 오목·오셀로 서버

Python, FastAPI, WebSocket으로 구현한 서버 권한형(authoritative) 오목·오셀로 서버입니다.
클라이언트는 `/ws`에 연결한 뒤 Lobby에서 서버가 관리하는 방을 생성하거나 선택해 입장합니다.

- 통신: WebSocket 단일 연결, JSON 메시지
- Lobby: 접속 직후 Room List 수신 및 실시간 갱신
- Room: 서버 생성 ID, 최대 99명, Player 2명과 Observer, 빈 방 자동 삭제
- 게임: 방 생성 시 `GOMOKU` 또는 `OTHELLO` 선택
- 오목: 서버에서 15x15 또는 19x19 선택, WHITE 선공과 렌주 금수(착수 금지)
- 오셀로: 표준 8x8, BLACK 선공, 자동 pass와 점수/종료 판정
- Ready: 두 플레이어가 모두 준비하면 시작, 다음 라운드는 직전 패배자가 WHITE
- GUI: 서버 시작/종료, 연결 및 방 현황, Room 목록, 로그 표시

## 문서

- [오셀로 Room별 보드 크기 선택 서버 개발 지시서](docs/othello-board-size-server-development-guide.md)
- [오셀로 Room별 보드 크기 선택 클라이언트 개발 지시서](../client/docs/othello-board-size-client-development-guide.md)
- [렌주 금수 착수 금지 전환 서버 개발 지시서](docs/renju-forbidden-block-server-development-guide.md)
- [렌주 금수 착수 금지 전환 클라이언트 개발 지시서](../client/docs/renju-forbidden-block-client-development-guide.md)
- [Ready 서버 계약](docs/ready-server-contract.md)
- [Room Player/Observer 서버 계약](docs/room-role-server-contract.md)
- [Room 채팅 서버 계약](docs/room-chat-server-contract.md)

## 요구 사항

- Python 3.11 이상
- FastAPI 0.115 이상
- uvicorn 0.30 이상
- GUI 사용 시 Tkinter

테스트 의존성을 포함한 전체 패키지는 `requirements.txt`에 정의되어 있습니다.

## 설치 및 실행

Windows 명령 프롬프트:

```bat
cd server
run.bat                          :: GUI 실행
run.bat --no-gui                 :: 헤드리스 15x15
run.bat --no-gui --board-size 19 :: 헤드리스 19x19
test.bat                         :: 전체 테스트
```

PowerShell:

```powershell
cd server
.\run.ps1
.\run.ps1 -BoardSize 19 -NoGui
.\run.ps1 -Test
.\run.ps1 -Install
```

스크립트는 `.venv`가 없으면 생성하고 필요한 패키지를 설치합니다. 수동 실행도 가능합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe server.py
.\.venv\Scripts\python.exe server.py --no-gui --board-size 19
```

직접 uvicorn을 실행할 때는 환경변수로 설정을 전달합니다.

```powershell
$env:GOMOKU_BOARD_SIZE = "19"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

지원 환경변수는 `GOMOKU_HOST`, `GOMOKU_PORT`, `GOMOKU_BOARD_SIZE`,
`GOMOKU_WIN_LENGTH`, `GOMOKU_STARTING_COLOR`입니다.

## Endpoint

| 종류 | 주소 | 설명 |
|---|---|---|
| WebSocket | `ws://127.0.0.1:8000/ws` | Lobby와 게임에서 계속 사용하는 단일 연결 |
| GET | `http://127.0.0.1:8000/` | 서버 설정과 WebSocket 경로 |
| GET | `http://127.0.0.1:8000/health` | 방, 연결, Lobby, 게임 참가자 수 |

WebSocket URL에 Room ID를 넣지 않습니다. 연결 자체와 Room 입장은 서로 분리되어 있습니다.

```text
/ws 연결
  → Lobby
  → connected
  → room_list
  → create_room 또는 join_room
  → 게임
  → leave_room
  → Lobby
```

## 서버 구조

```text
ConnectionManager
  - 전체 WebSocket과 ClientSession 관리
  - Lobby/Room 대상 메시지 전송

RoomManager
  - 서버의 전체 Room 목록 관리
  - Room ID 생성, 생성/입장/퇴장/삭제
  - Room List snapshot 생성

GameRoom
  - Room별 설정, 플레이어와 라운드 색상 관리
  - GomokuGame 또는 OthelloGame과 게임 lock 소유
```

각 `ClientSession`은 `LOBBY` 또는 `IN_ROOM` 상태입니다. 한 연결이 동시에 여러 Room에
속할 수 없고, 존재하지 않는 Room은 `join_room`의 부작용으로 생성되지 않습니다.

RoomManager lock은 방 생성·입장·퇴장·삭제를 직렬화합니다. GameRoom lock은 착수·승패·
Ready 같은 게임 상태 변경을 직렬화합니다.

## Room 수명과 상태

Room ID는 서버가 `room_001`, `room_002` 순으로 생성하며 삭제된 번호는 재사용하지 않습니다.
Room Name은 사용자가 정하는 표시 이름이고 Room 조회와 입장에는 불변 ID를 사용합니다.

| 상태 | 의미 |
|---|---|
| `WAITING` | 상대 입장 또는 두 플레이어의 Ready를 기다림 |
| `PLAYING` | 두 플레이어가 Ready를 완료해 게임 진행 중 |
| `FINISHED` | 게임 종료 후 다음 Ready 대기 중 |

- 모든 사용자는 Observer로 입장합니다.
- Observer가 Player 전환을 요청하면 빈 WHITE/BLACK 자리를 배정받습니다.
- Player 2명이 확정되고 양쪽이 Ready를 보내기 전에는 시작하지 않습니다.
- 한 명이 나가면 현재 라운드를 폐기하고 게임별 초기 보드의 `WAITING` 상태로 돌아갑니다.
- 마지막 플레이어가 나가면 RoomManager가 방을 제거합니다.
- Room은 생성 시 서버의 board size와 win length를 snapshot으로 보관합니다.

Room List에는 `room_id`, `room_name`, `game_type`, `board_size`, `win_length`, `players`,
`max_players`, `status`만 포함되며 WebSocket, lock, 보드 같은 내부 객체는 노출하지 않습니다.

Room Name은 서버가 NFKC로 정규화하고 양 끝 공백을 제거합니다. 정규화 후 1~30 Unicode
문자만 허용하며 제어 문자, 서로게이트, 줄/문단 구분 문자는 거부합니다. 중복은 정규화한
이름의 `casefold()` 값을 기준으로 비교하므로 대소문자나 호환 문자가 다른 같은 이름을
동시에 만들 수 없습니다. 방이 삭제되면 그 이름은 다시 사용할 수 있습니다.

## Client → Server

### Room List 요청

```json
{ "type": "get_room_list" }
```

### Room 생성

```json
{ "type": "create_room", "room_name": "친선 대국", "game_type": "GOMOKU" }
```

`game_type`은 `GOMOKU` 또는 `OTHELLO`입니다. 생략하면 구버전 호환을 위해 `GOMOKU`로
처리합니다. 클라이언트가 `board_size`, `win_length`, `room_id` 같은 추가 값을 보내도
무시하고 서버 설정과 서버 생성 ID를 사용합니다.

### Room 입장

```json
{ "type": "join_room", "room_id": "room_001" }
```

### Room 퇴장

```json
{ "type": "leave_room" }
```

### 착수

```json
{ "type": "move", "x": 7, "y": 7 }
```

Lobby에서 `move`나 `ready`를 보내면 `NOT_IN_ROOM`입니다. 좌표는 정수만
허용하며 클라이언트가 보내는 색상, 턴, 보드 상태는 신뢰하지 않습니다.

### 착수 되돌리기

진행 중인 오목 대국에서 자신의 최근 착수를 상대 동의로 되돌릴 수 있습니다.

```json
{ "type": "undo_request" }
{ "type": "undo_response", "accepted": true }
```

요청자가 방금 둔 상태면 1수, 상대가 둬 다시 요청자 차례가 된 상태면 양쪽 최신 수를
합쳐 2수를 제거합니다. 수락 후에는 항상 요청자 차례입니다. 자세한 계약은
[오목 착수 되돌리기 서버 계약](docs/gomoku-undo-server-contract.md)을 참고하십시오.

### Ready와 ping

```json
{ "type": "ready" }
```

최초 대국과 다음 대국 모두 두 플레이어의 Ready가 필요합니다. 자세한 계약은
[Ready 서버 계약](docs/ready-server-contract.md)을 참고하십시오.

```json
{ "type": "ping" }
```

## Server → Client

접속 직후 다음 두 메시지가 순서대로 옵니다.

```json
{
  "type": "connected",
  "supported_game_types": ["GOMOKU", "OTHELLO"],
  "game_type": "GOMOKU",
  "board_size": 15,
  "win_length": 5,
  "starting_color": "WHITE"
}
```

```json
{ "type": "room_list", "rooms": [] }
```

Room 정보가 바뀌면 Lobby 클라이언트에게 `room_list`를 다시 broadcast합니다. 갱신 이벤트는
Room 생성/삭제, 플레이어 입장/퇴장/disconnect, 게임 시작/종료, Ready입니다.

Room 생성 성공 시 생성자에게 다음 메시지가 이어집니다.

```json
{
  "type": "room_created",
  "room_id": "room_001",
  "room_name": "친선 대국",
  "game_type": "GOMOKU"
}
```

```json
{
  "type": "joined",
  "room_id": "room_001",
  "room_name": "친선 대국",
  "game_type": "GOMOKU",
  "your_color": "WHITE",
  "board_size": 15,
  "win_length": 5,
  "starting_color": "WHITE"
}
```

두 명이 모여도 즉시 시작하지 않습니다. 양쪽의 `ready`가 접수되면 `player_ready`,
`game_start`, `game_state`가 전송됩니다. 게임 중에는 `move_result`, `game_over`를 사용합니다. 퇴장과 연결 종료는
각각 `left_room`, 상대방에게 `player_disconnected`로 알립니다. ping 응답은 `pong`입니다.

`game_start`는 라운드마다 색상이 달라질 수 있으므로 각 플레이어에게 개별
`your_color`를 담아 전송합니다.

### 게임 메시지 payload

`game_type`, `board_size`, `win_length`, `starting_color`는 설정을 담는 네 메시지(`joined`,
`game_start`, `game_state`)에 공통으로 포함됩니다.

```json
{ "type": "player_joined", "color": "BLACK" }
{ "type": "player_ready", "color": "WHITE", "ready_count": 1, "required": 2 }
{ "type": "game_start", "game_type": "GOMOKU", "your_color": "WHITE", "board_size": 15, "win_length": 5, "starting_color": "WHITE", "current_turn": "WHITE" }
{ "type": "move_result", "game_type": "GOMOKU", "x": 7, "y": 7, "color": "WHITE", "next_turn": "BLACK" }
{ "type": "player_disconnected", "color": "BLACK" }
{ "type": "left_room", "room_id": "room_001" }
{ "type": "pong" }
```

게임이 끝난 착수는 `next_turn`이 `null`이고 곧바로 `game_over`가 이어집니다.

```json
{
  "type": "game_over",
  "game_type": "GOMOKU",
  "winner": "WHITE",
  "loser": "BLACK",
  "reason": "five_in_a_row",
  "message": "WHITE wins."
}
```

금수는 패배가 아니라 착수 금지이므로 `game_over`를 만들지 않습니다. 금수만 남아 제약
플레이어가 둘 곳이 전혀 없을 때만 다음 사유로 끝납니다.

```json
{
  "type": "game_over",
  "game_type": "GOMOKU",
  "winner": "BLACK",
  "loser": "WHITE",
  "reason": "no_forbidden_free_move",
  "message": "WHITE has no playable point left."
}
```

무승부는 `winner`와 `loser`가 모두 `null`입니다.

```json
{ "type": "game_over", "game_type": "GOMOKU", "winner": null, "loser": null, "reason": "draw", "message": "Draw." }
```

| 필드 | 설명 |
|---|---|
| `reason` | `five_in_a_row` / `no_forbidden_free_move` / `draw` |
| `message` | 그대로 표시 가능한 기본 안내 문구 |

오셀로 착수는 뒤집힌 좌표와 자동 pass 여부를 포함합니다. 이 메시지 직후 서버가
전체 `game_state`를 보내므로 클라이언트는 최종 보드와 합법 수를 재동기화합니다.

```json
{
  "type": "move_result",
  "game_type": "OTHELLO",
  "x": 2,
  "y": 3,
  "color": "BLACK",
  "next_turn": "WHITE",
  "flipped": [{"x": 3, "y": 3, "color": "BLACK"}],
  "passed_color": null
}
```

오셀로 `game_state`에는 `score`와 `legal_moves`가 추가되고, `game_over`에는 최종
`score`가 추가됩니다. `win_length`는 `null`입니다.

**오목도 착수마다** `move_result` 다음에 authoritative `game_state`가 이어집니다. 금수
목록이 매 수 달라지기 때문이며, 오셀로가 뒤집기·자동 pass를 동기화하는 방식과 같습니다.

전체 상태 동기화는 종료 정보까지 포함하며, 종료 상태에서는 `current_turn`이 `null`입니다.

```json
{
  "type": "game_state",
  "game_type": "GOMOKU",
  "board_size": 15,
  "win_length": 5,
  "starting_color": "WHITE",
  "board": [[null, "WHITE", null], ["BLACK", null, null]],
  "current_turn": "WHITE",
  "winner": null,
  "loser": null,
  "status": "PLAYING",
  "game_over_reason": null,
  "last_move": { "x": 7, "y": 7 },
  "constrained_color": "WHITE",
  "forbidden_moves": [
    { "x": 3, "y": 4, "forbidden_type": "DOUBLE_THREE" }
  ]
}
```

`board`는 `board_size`개의 행 배열이며 **`board[y][x]`** 순서입니다. 값은 `null`,
`"WHITE"`, `"BLACK"`입니다.

| 필드 | 게임 종류 | 설명 |
|---|---|---|
| `last_move` | 공통 | 마지막 착수 좌표 객체 또는 `null` |
| `constrained_color` | 오목만 | 렌주 금수가 적용되는 색. 자유룰이면 `null` |
| `forbidden_moves` | 오목만 | `constrained_color`가 둘 수 없는 빈 점 목록 |

`forbidden_moves`는 차례와 무관하게 항상 `constrained_color` 기준으로 계산하므로 두
플레이어가 같은 목록을 받습니다. 종료·대기 상태에서는 `[]`이고, `(y, x)` 오름차순으로
정렬해 보내지만 클라이언트는 순서에 의존해서는 안 됩니다.

두 플레이어가 Ready이면 플레이어별 `game_start`가 전송되고, 이어서 게임별 초기
보드의 `game_state`가 옵니다.

```json
{
  "type": "game_start",
  "your_color": "WHITE",
  "game_type": "GOMOKU",
  "board_size": 15,
  "win_length": 5,
  "starting_color": "WHITE",
  "current_turn": "WHITE"
}
```

클라이언트가 지켜야 할 점은 넷입니다.

1. 보드는 서버가 보낸 `move_result` / `game_state`로만 갱신한다.
2. 보드 크기는 `joined`(또는 `connected`)의 `board_size`를 사용한다.
3. `game_start`의 `your_color`로 자기 색을 갱신한다 (라운드마다 바뀔 수 있음).
4. 금수 표시는 `game_state`의 `forbidden_moves`만 신뢰한다. 렌주 판정을 클라이언트에서
   다시 구현하지 않는다.

## 오류 코드

| code | 발생 조건 |
|---|---|
| `INVALID_MESSAGE` | JSON 객체/type 형식 오류 또는 지원하지 않는 frame |
| `UNKNOWN_MESSAGE_TYPE` | 알 수 없는 type |
| `ROOM_NOT_FOUND` | 존재하지 않는 Room 입장 |
| `ROOM_FULL` | 두 명이 있는 Room 입장 |
| `NOT_IN_ROOM` | Lobby에서 게임/퇴장 메시지 전송 |
| `ALREADY_IN_ROOM` | Room 안에서 다른 Room 생성 또는 입장 |
| `INVALID_ROOM_NAME` | Room Name 누락, 타입/길이 오류 또는 금지 문자 |
| `ROOM_NAME_TAKEN` | NFKC 및 casefold 기준 중복 Room Name |
| `CREATE_ROOM_FAILED` | 예상하지 못한 내부 Room 생성 실패 |
| `INVALID_GAME_TYPE` | `GOMOKU`, `OTHELLO` 이외의 게임 종류 |
| `GAME_NOT_STARTED` | Ready가 완료되지 않은 상태에서 착수 |
| `READY_REQUIRES_TWO_PLAYERS` | 상대가 입장하기 전에 Ready 요청 |
| `READY_NOT_AVAILABLE` | 진행 중인 게임에서 Ready 요청 |
| `GAME_ALREADY_FINISHED` | 종료된 게임에 착수 |
| `INVALID_MOVE` | x/y가 정수가 아님 |
| `NOT_YOUR_TURN` | 상대 차례에 착수 |
| `OUT_OF_RANGE` | 보드 밖 좌표 |
| `POSITION_OCCUPIED` | 이미 사용된 좌표 |
| `FORBIDDEN_MOVE` | 제약 플레이어가 렌주 금수 자리에 착수 |
| `UNDO_NOT_AVAILABLE` | 진행 중인 오목에서 되돌릴 요청자 착수가 없음 |
| `UNDO_ALREADY_PENDING` | 이미 되돌리기 요청이 응답 대기 중 |
| `UNDO_PENDING` | 응답 대기 중 착수 또는 Ready 시도 |
| `NOT_UNDO_RESPONDER` | 요청자의 상대가 아닌 연결이 응답 |
| `UNSUPPORTED_GAME_OPERATION` | 오셀로 등 지원하지 않는 게임에서 되돌리기 요청 |

오류는 해당 연결에만 다음 형식으로 전송하며 WebSocket은 유지합니다.

```json
{
  "type": "error",
  "code": "ROOM_NOT_FOUND",
  "message": "The requested room does not exist."
}
```

`FORBIDDEN_MOVE`는 금수 종류와 좌표를 함께 실어 보냅니다. 돌은 놓이지 않고 차례도
그대로이며, 상대에게는 아무 메시지도 가지 않습니다.

```json
{
  "type": "error",
  "code": "FORBIDDEN_MOVE",
  "message": "Double-three is forbidden for WHITE.",
  "forbidden_type": "DOUBLE_THREE",
  "x": 7,
  "y": 7
}
```

## 게임 규칙

### 오목

- WHITE Player가 선공합니다.
- WHITE에게 3-3, 4-4, 장목 금수를 적용합니다.
- WHITE는 정확한 5목만 승리하며 BLACK은 5목 이상이면 승리합니다.
- **금수는 착수 금지입니다.** 서버가 `FORBIDDEN_MOVE`로 거부하고 돌은 놓이지 않으며
  차례와 보드가 그대로 유지된 채 게임이 계속됩니다.
- 5목을 만드는 수는 그것이 동시에 4-4여도 금수가 아니며 승리합니다.
- 장목이 애초에 막히므로 WHITE의 줄이 5를 넘는 일은 생기지 않습니다.
- 서버는 매 착수 후 `game_state`로 WHITE의 금수 좌표 전체를 알려 줍니다.
- 빈 자리가 전부 금수라 WHITE가 둘 곳이 없으면 `no_forbidden_free_move`로 WHITE가
  패배합니다. 실전에서는 사실상 도달하지 않는 상태입니다.
- 게임 종료 후 착수는 차단합니다.
- 다음 대국에서 양쪽이 Ready하면 직전 패배자를 WHITE로 배정합니다.
- 무승부이거나 패배자가 이미 WHITE면 색상을 유지합니다.

### 오셀로

- 8x8 보드 중앙에 WHITE/BLACK 두 돌씩 배치하고 BLACK이 선공합니다.
- 착수는 8방향 중 하나 이상에서 상대 돌을 감싸 뒤집을 수 있어야 합니다.
- 상대에게 합법 수가 없으면 서버가 자동 pass하고 같은 색이 다시 둡니다.
- 양쪽 모두 합법 수가 없거나 보드가 가득 차면 돌 수로 승패를 결정합니다.
- `move_result`는 `flipped`, `passed_color`를 포함하고, 곧이어 authoritative
  `game_state`가 전송됩니다.
- 오셀로 `game_state`는 `score`와 현재 턴의 `legal_moves`를 포함합니다.
- 종료 사유는 `no_legal_moves` 또는 `board_full`이고 `game_over`에 최종 `score`가 포함됩니다.
- 다음 대국에서 양쪽이 Ready하면 직전 패배자가 BLACK을 받습니다. 동점이면 색상을 유지합니다.

## 서버 GUI

GUI에서 Host, Port, Board Size를 선택한 뒤 서버를 시작합니다. 실행 중 설정 입력은
비활성화됩니다.

상태 영역의 의미는 다음과 같습니다.

- Connections: Lobby를 포함한 전체 WebSocket 수
- In Lobby: Room에 들어가지 않은 연결 수
- Players in Rooms: Room 좌석을 차지한 플레이어 수
- Active Rooms: RoomManager가 보유한 방 수

Room 표에는 Room ID, Room Name, 게임 종류, 보드 크기, 인원, 상태가 표시됩니다. uvicorn은 작업 스레드에서
동작하고 Tk 위젯 갱신은 `root.after`를 통해 GUI 스레드에서만 수행합니다.

## 프로젝트 구조

```text
server/
├── app/
│   ├── main.py          # /ws endpoint와 메시지 dispatch
│   ├── connection.py    # ClientSession, ConnectionManager
│   ├── room_manager.py  # 서버 소유 Room registry
│   ├── room.py          # Room 플레이어, 색상, 게임 상태
│   ├── room_name.py     # Room Name 정규화와 검증
│   ├── game_type.py     # GOMOKU / OTHELLO 식별자
│   ├── game.py          # GomokuGame과 공통 결과 타입
│   ├── othello.py       # OthelloGame
│   ├── rules.py         # RenjuRule / FreeStyleRule
│   ├── protocol.py      # 입력 parsing과 출력 message builder
│   └── config.py        # 서버 및 Room 설정
├── gui/
│   ├── controller.py
│   └── server_gui.py
├── tests/
├── server.py
└── requirements.txt
```

## 테스트

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest -q tests\test_websocket.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_room_manager.py
```

테스트는 Room 생성/입장/퇴장/삭제, Room Name 정규화와 동시 중복 생성 방지, Lobby broadcast,
disconnect 정리, 마지막 자리 입장 경쟁, 동시 착수, 렌주 금수 거부와 금수 목록 광고,
전수 스캔 prefilter 동치성, Ready 기반 색상 교환, 오목 1수/2수 되돌리기 협상과 턴 복원,
15x15/19x19 오목, 8x8 오셀로 뒤집기·자동 pass·종료, 혼합 Room과 GUI controller를
포함합니다.

## 최소 클라이언트 예시

```python
import asyncio
import json

import websockets


async def main() -> None:
    async with websockets.connect("ws://127.0.0.1:8000/ws") as ws:
        print(json.loads(await ws.recv()))  # connected
        print(json.loads(await ws.recv()))  # room_list

        await ws.send(
            json.dumps({
                "type": "create_room",
                "room_name": "친선 대국",
                "game_type": "GOMOKU",
            })
        )

        async for raw in ws:
            message = json.loads(raw)
            print(message)

            if message["type"] == "game_start":
                await ws.send(json.dumps({"type": "move", "x": 7, "y": 7}))


asyncio.run(main())
```

클라이언트는 두 플레이어가 입장한 뒤 `ready`를 보내야 합니다. 보드와 턴은 자체 확정하지
않고 서버가 보낸 `game_start`, `game_state`, `move_result`를 기준으로 갱신해야 합니다.
