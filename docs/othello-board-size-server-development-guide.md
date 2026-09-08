# 오셀로 Room별 보드 크기 선택 서버 개발 지시서

## 1. 목적과 범위

오셀로 Room을 만들 때 Room 생성자가 보드 크기를 선택할 수 있도록 서버를 확장한다.
오목 보드 크기는 계속 서버 운영 설정(`ServerConfig`)이 결정하며, 클라이언트가 선택할 수
없다.

이 문서는
[`client/docs/othello-board-size-client-development-guide.md`](../../client/docs/othello-board-size-client-development-guide.md)
의 서버 측 대응 계약이다. 와이어 계약 문구가 두 문서에서 어긋나면 클라이언트 문서를
기준으로 맞춘 뒤 두 문서를 함께 갱신한다.

초기 서버 정책은 다음과 같다.

```text
SUPPORTED_OTHELLO_BOARD_SIZES = (8, 10, 12)
DEFAULT_OTHELLO_BOARD_SIZE    = 10
LEGACY_OTHELLO_BOARD_SIZE     = 8
```

이 문서는 코드 변경 지시만 담는다. 실제 적용은 별도 작업으로 수행한다.

## 2. 현재 코드 진단

### 2.1 이미 크기에 무관한 부분 (재작성 불필요)

- `app/board.py`의 `create_board(board_size)`, `in_bounds(board_size, x, y)`는 크기를
  파라미터로 받는다.
- `app/othello.py:72`의 `middle = self.board_size // 2`는 초기 4돌 배치의 일반식이다.
  짝수 크기라면 8/10/12 모두 그대로 동작한다.
- `_flips_for_move`, `legal_moves`, `score`, `move_count == board_size * board_size`
  종료 판정 모두 `self.board_size` 기반이다.
- `app/protocol.py:225`의 `settings_fields()`가 `joined`, `game_start`, `game_state`,
  `restart`에 공통으로 들어가므로 이 네 메시지는 코드 변경 없이 실제 Room 크기를 전달한다.
- `GameRoom`은 생성 시 settings를 복사해 수명 동안 고정하고 `reset()`이 `board_size`를
  건드리지 않으므로 "Restart에도 크기 불변"이 이미 보장된다.

따라서 엔진 재작성이 아니라 고정 상수, Room 생성 경로, capability 광고만 변경한다.

### 2.2 풀어야 하는 고정 결합

| 위치 | 현재 | 문제 |
| --- | --- | --- |
| `app/config.py:34` | `OTHELLO_BOARD_SIZE = 8` | 단일 상수라 allowlist 표현 불가 |
| `app/config.py:62-63` | `board_size != 8` → `ConfigError` | 10x10 `GameSettings` 생성 자체가 불가 |
| `app/config.py:166-175` | `settings_for(game_type)` | 호출부가 없는 dead code |
| `app/room_manager.py:126-131` | Othello settings 인라인 하드코딩 | `settings_for`와 로직 중복 |
| `app/othello.py:32` | `self.board_size = OTHELLO_BOARD_SIZE` | `from_settings`가 `settings.board_size`를 무시 |
| `app/othello.py:160` | `"Coordinates must be within 0..7."` | 오류 메시지 하드코딩 |
| `app/protocol.py:101` | `CreateRoomCommand` | `board_size` 필드 없음 |
| `app/protocol.py:141-147` | 파서 docstring | "create_room의 board_size는 무시한다"고 명시 |
| `app/protocol.py:235` | `connected()` | `room_creation_options` 없음 |
| `app/protocol.py:265` | `room_created()` | `board_size`가 없음 (오목 포함 기존 계약 미달) |

## 3. 결정 대기 항목

아래 5개는 구현 착수 전에 확정한다. 이 문서의 나머지 절은 **권고안 기준**으로 작성되어
있으므로, 다른 선택을 하면 해당 절을 함께 수정한다.

| # | 논점 | 권고안 | 대안 |
| --- | --- | --- | --- |
| D1 | `GameSettings`의 검증 강도 | 구조 불변식(짝수, 4 이상)만 검증하고 allowlist는 정책 레이어로 분리 | `GameSettings`에서 allowlist까지 검증 |
| D2 | allowlist의 런타임 가변성 | 프로세스 수명 동안 불변. 클라이언트 문서 §6의 Lobby allowlist 검증을 구조 검증으로 완화 | 런타임 변경 허용 + 재광고 메시지 신설 |
| D3 | GOMOKU 요청에 섞여 온 `board_size` | 조용히 무시 (파싱 자체를 하지 않음) | `INVALID_BOARD_SIZE`로 거부 |
| D4 | 서버 GUI의 allowlist 편집 | 표시만 (D2와 일관) | 운영자 편집 허용 |
| D5 | allowlist 범위 | `(8, 10, 12)` | `(6, 8, 10, 12, 14, 16)` 등 확장 |

D5는 코드 변경 없이 상수만 늘리면 되므로 나중에 확장할 수 있다.

## 4. 정책 모델

### 4.1 상수

`app/config.py`의 `OTHELLO_BOARD_SIZE`를 다음 상수들로 교체한다. 참조가 6곳뿐이므로
alias를 남기지 않고 개명한다.

```python
#: Othello 보드는 중앙 2x2 초기 배치가 성립해야 하므로 짝수여야 한다.
MIN_OTHELLO_BOARD_SIZE: Final[int] = 4

#: Room 생성 시 클라이언트가 고를 수 있는 크기.
SUPPORTED_OTHELLO_BOARD_SIZES: Final[tuple[int, ...]] = (8, 10, 12)

#: 신규 클라이언트에 광고하는 기본 선택값.
DEFAULT_OTHELLO_BOARD_SIZE: Final[int] = 10

#: board_size를 보내지 않는 구버전 클라이언트에 적용하는 값.
LEGACY_OTHELLO_BOARD_SIZE: Final[int] = 8

OTHELLO_STARTING_COLOR: Final[Color] = Color.BLACK
```

`DEFAULT_OTHELLO_BOARD_SIZE`와 `LEGACY_OTHELLO_BOARD_SIZE`가 다른 것은 의도된
설계다. 근거는 8절에 기록한다.

### 4.2 `RoomCreationPolicy`

현재 `RoomManager`는 `ServerConfig.game_settings`(오목 전용 `GameSettings`)만 받고,
Othello settings는 `room_manager.py:126`에서 상수로 직접 만든다. Othello가 크기를 갖게
되면 `RoomManager`가 allowlist를 알아야 하므로 정책 값 객체를 도입한다.

```python
@dataclass(frozen=True)
class RoomCreationPolicy:
    """Room 생성 시 적용되는 서버 정책.

    ``gomoku``는 운영자가 GUI에서 런타임 변경할 수 있고, Othello allowlist는
    프로세스 수명 동안 불변이다(D2).
    """

    gomoku: GameSettings = field(default_factory=GameSettings)
    othello_board_sizes: tuple[int, ...] = SUPPORTED_OTHELLO_BOARD_SIZES
    othello_default_board_size: int = DEFAULT_OTHELLO_BOARD_SIZE

    def __post_init__(self) -> None:
        """allowlist를 검증한다: 비어 있지 않고, 중복이 없고, 오름차순이고,
        모두 짝수이며 MIN_OTHELLO_BOARD_SIZE 이상이고, 기본값이 목록에 있어야 한다."""

    def settings_for(
        self, game_type: GameType, board_size: object = None
    ) -> GameSettings:
        """Room 하나의 불변 settings를 만드는 유일한 진입점."""

    def advertisement(self) -> dict[str, Any]:
        """connected.room_creation_options에 그대로 들어가는 dict."""

    def with_gomoku(self, settings: GameSettings) -> "RoomCreationPolicy":
        """오목 절반만 교체한 새 정책 (GUI 런타임 설정 변경용)."""
```

요구 사항:

- `settings_for()`가 크기 해석, 검증, `GameSettings` 생성의 **유일한** 진입점이다.
  `config.py:166`의 dead code를 이 메서드로 되살리고 `room_manager.py:126-131`의
  중복 로직을 제거한다.
- `connected` 광고와 생성 시 검증이 **같은 객체**를 읽으므로 절대 어긋날 수 없다.
  광고 값을 `protocol.py` 상수로 따로 두지 않는다.
- `RoomManager`에 `ServerConfig`를 통째로 넘기지 않는다. `RoomManager`가 host/port를
  알게 되어 레이어링이 나빠진다.

`settings_for()`의 동작:

```text
game_type is OTHELLO:
    board_size is None      -> LEGACY_OTHELLO_BOARD_SIZE (구버전 호환, 8절)
    int이 아니거나 bool     -> InvalidBoardSizeError
    othello_board_sizes 밖  -> InvalidBoardSizeError
    통과                    -> GameSettings(OTHELLO, board_size, None, BLACK)

game_type is GOMOKU:
    board_size 인자를 무시하고 self.gomoku를 반환 (D3)
```

### 4.3 레이어별 검증 책임 (D1)

allowlist를 `GameSettings`에 넣으면 도메인 값 객체가 서버 운영 정책을 알게 되고,
테스트에서 16x16 Othello 엔진을 만드는 것도 불가능해진다. 책임을 둘로 나눈다.

```text
GameSettings.__post_init__      구조 불변식만
  OTHELLO: board_size가 짝수이고 MIN_OTHELLO_BOARD_SIZE 이상
           win_length is None
           starting_color is BLACK
  GOMOKU:  기존 규칙 유지

RoomCreationPolicy.settings_for  운영 정책
  OTHELLO: board_size in othello_board_sizes
```

정책 밖 크기는 정책 레이어에서, 물리적으로 불가능한 크기는 도메인에서 막히는 이중
방어다. 홀수 거부는 정책이 아니라 도메인 규칙이다. 중앙 2x2 초기 배치가 성립하지 않기
때문이다.

## 5. WebSocket 계약 변경

### 5.1 `connected`에 생성 옵션 광고

```json
{
  "type": "connected",
  "supported_game_types": ["GOMOKU", "OTHELLO"],
  "room_creation_options": {
    "OTHELLO": {
      "board_sizes": [8, 10, 12],
      "default_board_size": 10
    }
  },
  "game_type": "GOMOKU",
  "board_size": 15,
  "win_length": 5,
  "starting_color": "WHITE"
}
```

- 최상위 `game_type`, `board_size`, `win_length`, `starting_color`는 기존과 같이 오목
  운영 설정이다. 의미를 바꾸지 않는다.
- `room_creation_options`에는 클라이언트가 **선택할 수 있는 값만** 넣는다. 오목의
  `board_size`는 운영 설정이므로 `GOMOKU` key를 넣지 않는다.
- `SUPPORTED_GAME_TYPES`에 없는 게임 key는 광고하지 않는다.
- `board_sizes`는 오름차순 정렬된 정수 배열로 직렬화한다.

### 5.2 `create_room` 요청

```json
{
  "type": "create_room",
  "room_name": "10칸 오셀로",
  "game_type": "OTHELLO",
  "board_size": 10
}
```

오목 요청에는 `board_size`를 넣지 않는다. 클라이언트가 실수로 넣어도 서버는 무시하고
운영 설정을 사용한다(D3).

```json
{
  "type": "create_room",
  "room_name": "오목 대국",
  "game_type": "GOMOKU"
}
```

### 5.3 `room_created`에 `board_size` 추가

`app/protocol.py:265`의 `room_created()`는 현재 `room_id`, `room_name`, `game_type`만
보낸다. 오목 포함 **기존 계약도 미달** 상태다.

```json
{
  "type": "room_created",
  "room_id": "room_010",
  "room_name": "10칸 오셀로",
  "game_type": "OTHELLO",
  "board_size": 10
}
```

`settings_fields()`를 그대로 펼치지 않는다. `win_length`와 `starting_color`까지 붙어
클라이언트 문서의 예시와 달라진다. **`board_size` 한 필드만** 추가하고
`app/main.py:228` 호출에 `room.board_size`를 넘긴다. 구버전 클라이언트는 추가 필드를
무시하므로 하위 호환은 안전하다.

`joined`, `game_start`, `game_state`, `restart`, `room_list`는 `settings_fields()`와
`RoomSummary`를 통해 이미 실제 크기를 전달하므로 변경하지 않는다.

### 5.4 오류: `INVALID_BOARD_SIZE`

`parse_client_message()`는 settings를 받지 않는 순수 함수라 allowlist를 모른다. 형태와
정책을 분리하되 **와이어 코드는 하나로 통일**한다.

```text
파서 (protocol.py)
  board_size가 int이 아니거나 bool  -> ProtocolError(INVALID_BOARD_SIZE)
  기존 is_integer_coordinate() 재사용으로 True / 1.0 / "10" 거부

정책 (RoomCreationPolicy)
  짝수 / 최소값 / allowlist 위반    -> InvalidBoardSizeError(code="INVALID_BOARD_SIZE")
```

`app/main.py:203`의 `except GameError as exc: ... exc.code`가 이미 코드를 그대로 와이어로
흘려보내므로 **`_handle_create_room` 핸들러 로직은 변경하지 않는다.** 클라이언트는 두
경로에서 동일한 `INVALID_BOARD_SIZE` 하나만 본다.

오류 우선순위를 확정한다. **`INVALID_ROOM_NAME` → `INVALID_GAME_TYPE` →
`INVALID_BOARD_SIZE` → `ALREADY_IN_ROOM` → `ROOM_NAME_TAKEN`** 순서로 검사한다. 앞의
셋은 파싱 단계, 뒤의 둘은 실행 단계이므로 이 순서가 자연히 성립한다.

## 6. 파일별 작업 지시

### `app/config.py`

- `OTHELLO_BOARD_SIZE`를 4.1의 상수들로 교체
- `GameSettings.__post_init__`의 Othello 분기를 구조 불변식으로 완화 (4.3)
- `RoomCreationPolicy` 추가 (4.2)
- `ServerConfig`에 `othello_board_sizes`, `othello_default_board_size` 필드 추가
- `ServerConfig.settings_for()`를 제거하고 `room_creation_policy` property로 대체
- `from_env()`: `GOMOKU_OTHELLO_BOARD_SIZES="8,10,12"`,
  `GOMOKU_OTHELLO_DEFAULT_BOARD_SIZE="10"` 파싱. 빈 문자열/공백은 기본값으로 처리하고
  파싱 실패는 `ConfigError`
- `as_env()`: 위 두 변수를 왕복 가능하게 출력. reload worker가 정책을 상속해야 한다
- 접두어는 `GOMOKU_`를 유지한다. Othello에 어색하지만 `from_env`/`as_env` 왕복 계약이
  걸려 있어 변경 위험이 크다

### `app/errors.py`

```python
class InvalidBoardSizeError(GameError):
    code = "INVALID_BOARD_SIZE"
    default_message = "The requested board size is not available."
```

### `app/othello.py`

- `__init__(self, board_size: int = LEGACY_OTHELLO_BOARD_SIZE, status=...)`로 크기 주입
- `from_settings()`가 `settings.board_size`를 실제로 사용 (현재 무시)
- 160행 오류 메시지를 `f"Coordinates must be within 0..{self.board_size - 1}."`로 변경
- `_place_initial_stones()`, `_flips_for_move()`, `legal_moves()`, `score`,
  `move_count == board_size * board_size` 종료 판정은 변경하지 않는다
- `reset()`이 `board_size`를 바꾸지 않는 현재 동작을 유지한다

### `app/room.py`

- 변경 없음. `GameRoom.__init__`의 Othello 분기(113-121행)가 이미 `board_size`를
  `GameSettings`에 담아 `OthelloGame.from_settings()`로 넘기므로, `from_settings`가
  크기를 존중하기만 하면 그대로 동작한다
- `RoomSummary`도 이미 `board_size`를 담고 있어 변경 없음

### `app/room_manager.py`

- `__init__`이 `GameSettings` 대신 `RoomCreationPolicy`를 받는다
- `settings` property는 하위 호환을 위해 `policy.gomoku`를 반환하고,
  `creation_policy` property를 새로 노출한다
- `update_settings(settings)`는 `policy.with_gomoku(settings)`로 오목 절반만 교체한다.
  Othello allowlist는 런타임에 바뀌지 않는다(D2)
- `create_room(connection_id, room_name, game_type, board_size=None)`로 확장
- 126-131행 인라인 하드코딩을 `self._policy.settings_for(game_type, board_size)` 호출로
  교체
- **크기 검증을 락 진입 전에** 수행한다. `normalize_room_name()`과 같은 위치다.
  124행 `_next_room_id()`가 락 안에서 카운터를 먼저 올리므로, 검증을 그 뒤에 두면 거부된
  요청이 room id를 태운다

### `app/protocol.py`

- `ErrorCode`에 `INVALID_BOARD_SIZE` 추가
- `CreateRoomCommand`에 `board_size: Optional[int] = None` 추가
- `parse_client_message()`의 `create_room` 분기: `game_type is OTHELLO`일 때만
  `board_size`를 읽는다. GOMOKU면 필드를 **아예 파싱하지 않는다**(D3). 그래야 커맨드에
  "무시될 값"이 실려 다니지 않고 "Gomoku 커맨드에 board_size가 있으면 버그"를 테스트로
  못박을 수 있다
- 141-147행 파서 docstring 수정. "board_size는 무시한다"가 더 이상 사실이 아니다
- `connected(settings, policy)`로 시그니처 확장, `room_creation_options` 추가
- `room_created(room_id, room_name, game_type, board_size)`로 확장 (5.3)

### `app/main.py`

- 112행: `protocol.connected(rooms.settings, rooms.creation_policy)`
- 202행: `create_room(..., command.game_type, command.board_size)`
- 228행: `protocol.room_created(..., room.board_size)`
- `_handle_create_room`의 예외 처리 로직은 변경하지 않는다 (5.4)
- `RoomManager(server_config.game_settings)` →
  `RoomManager(server_config.room_creation_policy)`
- `create_app`의 `version="1.3.0"` → `"1.4.0"`
- `GET /`와 `/health`에 광고 중인 Othello 정책을 노출한다. 운영자가 WebSocket 없이
  정책을 확인할 수 있다

### `gui/server_gui.py`, `gui/controller.py`

- Othello allowlist는 **표시만** 한다(D4). 라벨 예: `Othello  8 / 10 / 12 (기본 10)`
- 오목 크기 라디오(15/19)와 Room 목록의 `{board_size}x{board_size}` 표시(320행)는 이미
  동적이므로 변경하지 않는다
- `ServerConfig`의 새 필드에 기본값이 있으므로 258행 `ServerConfig(...)` 생성부는
  변경하지 않아도 동작한다

### 문서

- `server/README.md` 11행 "오셀로: 표준 8x8" → 선택 가능한 크기로 수정
- 같은 파일 148-149행(클라이언트 추가 필드 무시 설명), 190행 `connected` 예시, 298행
  Othello 예시, 366행 오류 코드 표, 398행 게임 규칙, 457행 테스트 요약 갱신
- 이 문서 3절의 결정 결과를 확정 내용으로 반영
- 클라이언트 문서 §6 수정 요청 (7절)

## 7. 클라이언트 문서 수정 요청

클라이언트 문서 §6은 **Lobby Room의 OTHELLO `board_size`가 협상된 allowlist 안에 있는지**
검증하라고 되어 있다. 이 규칙은 정상 서버의 정상 `room_list` 때문에 클라이언트가
protocol error를 내는 경로를 만든다.

`board_size`는 **방이 생성된 시점의 정책**의 산물인데, 클라이언트는 **현재 협상된**
정책으로 검증하기 때문이다. 구체적으로:

- 구버전 클라이언트가 만든 Othello 방은 항상 8x8이다. 운영자가 allowlist를
  `[10, 12]`로 좁히면 그 8x8 방이 협상된 목록 밖이 된다.
- 운영자가 재시작으로 allowlist를 좁히면 그 이전 정책으로 만들어진 방이 목록에 남을 수
  있다.

권고 수정안:

```text
allowlist 멤버십 검증  -> create_room 요청을 만들 때만 적용
room_list의 방         -> 구조 검증만 (짝수, 4 이상)
joined 이후            -> 동일 Room 내 크기 일관성 검증 유지 (현행 유지)
```

서버 측 대응은 D2다. allowlist를 프로세스 수명 동안 불변으로 고정하고, GUI에서 런타임
편집을 제공하지 않는다. 실행 중인 방을 리사이즈하지 않는 기존 원칙과 같은 이유다.

## 8. 호환성 정책

### 8.1 `board_size` 생략 시 8을 쓰는 이유

광고 기본값은 10인데 생략 시 fallback은 8이다. 즉 "광고 기본값 != 생략 fallback"이며,
이것은 의도된 설계다.

`board_size`를 보내지 않는 클라이언트는 정의상 구버전이고, 구버전 클라이언트는
`board_size == 8`을 엄격 검증한다. 여기에 10x10을 주면 `joined` 수신 즉시 protocol
error가 난다. 따라서 생략은 "구버전 신호"로 해석하고 8을 적용한다.

이 근거를 `settings_for()` 구현에 주석으로 남긴다.

### 8.2 혼재 상태는 정상 동작이다

신규 클라이언트의 Lobby에는 8x8 방(구버전 클라이언트 생성)과 10x10, 12x12 방이 섞여
보인다. 이는 정상이며 클라이언트는 각 방의 `board_size`를 그대로 표시한다.

### 8.3 불변식

- Room의 `board_size`는 생성 시 확정되고 Restart를 포함한 Room 수명 동안 바뀌지 않는다.
- 운영자의 오목 설정 변경은 **이후 생성되는** 방에만 적용된다 (기존 동작).
- Othello allowlist는 프로세스 수명 동안 불변이다 (D2).

## 9. 테스트 요구사항

### `tests/test_config.py`

현재 Othello 관련 테스트가 **0건**이다. 신규 작성한다.

- `GameSettings(OTHELLO, board_size=10/12)` 수용
- 홀수, 4 미만, `win_length` 비-null, `starting_color=WHITE` 거부
- `RoomCreationPolicy.__post_init__`: 빈 목록, 중복, 홀수 포함, 목록 밖 기본값 거부
- `settings_for(OTHELLO, 10)` 수용, `(OTHELLO, 9)`와 `(OTHELLO, 14)`는
  `InvalidBoardSizeError`
- `settings_for(OTHELLO, None)` → 8
- `settings_for(OTHELLO, True)`, `(OTHELLO, "10")` → `InvalidBoardSizeError`
- `settings_for(GOMOKU, 10)`이 운영 설정을 반환 (인자 무시)
- `advertisement()`가 오름차순 정렬된 `board_sizes`와 목록 내 `default_board_size`를 반환
- `from_env()` / `as_env()` 왕복. 빈 문자열, 공백, 비정수, 목록 밖 기본값 처리
- `with_gomoku()`가 Othello 절반을 보존

### `tests/test_othello.py`

80행이 8x8 하드코딩이다. 크기를 파라미터화한다.

- 8/10/12 각각 초기 4돌 위치와 `move_count == 4`
- 각 크기에서 첫 합법 수 개수와 뒤집기 결과
- 경계 좌표 `(0, 0)`, `(size-1, size-1)` 처리와 `size` 좌표 거부
- `OutOfRangeError` 메시지가 크기에 맞게 나오는지
- 각 크기에서 `BOARD_FULL` 및 `NO_LEGAL_MOVES` 종료 판정
- `reset()` 후에도 `board_size`가 유지되는지
- `from_settings()`가 `settings.board_size`를 실제로 반영하는지 (회귀 방지)

### `tests/test_room_manager.py`

`create_room()` 시그니처 변경으로 호출부 다수가 영향받는다.

- 10x10 / 12x12 Othello 방 생성 후 `room.board_size`와 `summary().board_size` 확인
- 허용 밖 크기 → `InvalidBoardSizeError`, **room id 카운터가 증가하지 않음**
- 거부된 요청이 `_room_ids_by_name`에 이름을 예약하지 않음
- `board_size=None` → 8
- Gomoku 방 생성에 `board_size`를 넘겨도 운영 설정 유지
- 서로 다른 크기의 Othello 방 2개가 동시에 존재하고 상태가 섞이지 않음
- `update_settings()`가 Othello allowlist를 바꾸지 않음

### `tests/test_protocol.py`

- 500행 `room_created` 기대값 갱신 (`board_size` 추가)
- `connected`의 `room_creation_options` 구조. `GOMOKU` key가 없는지 확인
- `create_room` 파싱: Othello + 정수, Othello + 생략(`None`), Othello + `True`,
  Othello + `"10"`, Othello + 홀수(파서 통과 후 정책에서 거부)
- Gomoku + `board_size` → 커맨드에 `board_size`가 실리지 않음
- `INVALID_BOARD_SIZE` 코드 문자열

### `tests/test_websocket.py`

- 10x10 Othello 방 생성 → `room_created`, `joined`, `game_start`, `game_state`,
  `room_list`, Restart 후 `restart`까지 전 구간에서 `board_size == 10`
- 12x12에서 실제 착수 → `move_result.flipped`와 후속 `game_state` 정합
- 허용 밖 크기 → `INVALID_BOARD_SIZE` 수신 후에도 세션이 Lobby에 유지되고 다음 요청이
  정상 처리됨
- `board_size` 생략 → 8x8 방 생성
- 두 클라이언트가 각각 8x8과 12x12 방을 만들고 서로의 상태에 영향이 없음
- `connected`의 `room_creation_options`가 서버 설정과 일치

## 10. 구현 순서

1. `config.py`: 상수 교체, `GameSettings` 구조 검증 완화, `RoomCreationPolicy`
2. `errors.py`, `protocol.ErrorCode`: `INVALID_BOARD_SIZE`
3. `othello.py`: `board_size` 주입, `from_settings` 수정, 오류 메시지 동적화
4. `room_manager.py`: 정책 보유, 인라인 하드코딩 제거, 락 진입 전 검증
5. `protocol.py`: `CreateRoomCommand`, 파서, `connected` 광고, `room_created`
6. `main.py`: 호출 인자, `RoomManager` 생성, HTTP endpoint, 버전
7. env 계약과 GUI 표시
8. `README.md`와 클라이언트 문서 동기화
9. 단위 테스트 → WebSocket 통합 테스트
10. 실제 클라이언트 2개로 8/10/12 혼재 수동 검증

## 11. 완료 조건

- 클라이언트가 `create_room`에 보낸 8/10/12로 Othello 방이 생성된다.
- `connected.room_creation_options`가 실제 서버 정책과 항상 일치한다.
- 허용 밖 크기, 홀수, boolean, 문자열이 `INVALID_BOARD_SIZE`로 거부되고 세션은 유지된다.
- 거부된 요청이 room id와 방 이름을 소비하지 않는다.
- `room_created`가 `board_size`를 포함한다 (오목 포함).
- Room의 크기가 Restart를 포함한 수명 동안 불변이다.
- `board_size`를 보내지 않는 구버전 클라이언트가 8x8로 정상 동작한다.
- Gomoku 방 생성 동작과 운영자 오목 설정 경로가 변하지 않는다.
- 서로 다른 크기의 Othello 방이 동시에 존재해도 상태가 섞이지 않는다.
- 자동 테스트 전체 통과와 혼재 수동 검증 결과가 기록된다.
