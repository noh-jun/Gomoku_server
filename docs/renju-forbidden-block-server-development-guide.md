# 렌주 금수 착수 금지 전환 서버 개발 지시서

## 1. 목적과 범위

렌주 금수(3-3, 4-4, 장목)를 **즉시 패배**로 처리하던 현재 동작을 **착수 금지**로 바꾼다.
금수 자리는 서버가 거부하고, 돌은 보드에 놓이지 않으며, 차례는 그대로 유지되고, 게임은
계속된다. 서버는 제약 플레이어의 금수 좌표 목록을 상태 메시지에 실어 보내 클라이언트가
GUI에 미리 표시할 수 있게 한다.

이 문서는
[`client/docs/renju-forbidden-block-client-development-guide.md`](../../client/docs/renju-forbidden-block-client-development-guide.md)
의 서버 측 대응 계약이다. 와이어 계약 문구가 두 문서에서 어긋나면 이 문서를 기준으로 맞춘 뒤
두 문서를 함께 갱신한다.

이 문서는 코드 변경 지시만 담는다. 실제 적용은 별도 작업으로 수행한다.

### 1.1 한 줄 요약

| | 현재 | 변경 후 |
| --- | --- | --- |
| 금수 착수 | 돌을 놓고 `game_over`(`forbidden_move`)로 패배 | `error`(`FORBIDDEN_MOVE`)로 거부, 보드·차례 불변 |
| 금수 사전 안내 | 없음 | `game_state.forbidden_moves`로 좌표+종류 제공 |
| 장목 | 두면 패배 | 애초에 둘 수 없음 (제약 플레이어는 6목 이상을 만들 수 없다) |

### 1.2 범위 밖

- 오셀로 로직. `OthelloGame`은 금수 개념이 없고, 이 변경으로 동작이 달라지지 않는다.
- `FreeStyleRule`. 금수가 없으므로 새 API의 기본 구현(빈 결과)을 그대로 상속한다.
- Room/Lobby/Restart 수명주기. 색 재배정은 계속 `winner`/`loser`만 본다.
- 서버 GUI. 표시 항목 변경 없음.

## 2. 현재 코드 진단

### 2.1 금수가 "패배"로 굳어 있는 지점

| 위치 | 현재 | 변경 방향 |
| --- | --- | --- |
| `app/rules.py:52-60` | `RuleVerdict.forbidden` + `is_forbidden` | `RuleVerdict`는 `win`만 남긴다 |
| `app/rules.py:156-181` | `evaluate_move`가 금수를 판정해 반환 | 승리 판정만 남긴다 |
| `app/rules.py:222-234` | `is_forbidden()`이 bool만 반환 | 종류를 반환하는 `_classify_placed()`로 승격 |
| `app/game.py:52` | `GameOverReason.FORBIDDEN_MOVE` | 제거, `NO_FORBIDDEN_FREE_MOVE` 신설 |
| `app/game.py:85, 97-98` | `MoveResult.forbidden_type` / `is_forbidden` | 제거 |
| `app/game.py:138, 200, 226` | `GomokuGame.forbidden_type` 상태 | 제거 |
| `app/game.py:302-307` | 금수 → `_finish(winner=opponent)` | 착수 전 검증으로 이동 |
| `app/protocol.py:337-339` | `describe_game_over`의 금수 패배 문구 | 제거 |
| `app/protocol.py:372-374` | `game_over`의 `forbidden_type`/`x`/`y` | 제거 |
| `app/protocol.py:394-396` | `game_state.forbidden_type` | 제거, `forbidden_moves`로 대체 |
| `app/main.py:387-393` | 금수 패배 로그 | 금수 거부 로그로 변경 |
| `app/othello.py:41, 95` | `self.forbidden_type = None` | 프로토콜에서 필드가 사라지므로 제거 |

### 2.2 이미 재사용 가능한 부분 (재작성 불필요)

- `RenjuRule.is_forbidden()`(`rules.py:222`)은 **이미 착수 전 판정에 필요한 의미론**을 갖고
  있다. 돌이 놓인 상태를 전제로 장목 → 정확한 5 → 4-4 → 3-3 순으로 검사하고, **5목을
  만드는 수는 금수로 처벌하지 않는다**. 이 규칙은 그대로 유지한다.
- `_completion_is_forbidden()`(`rules.py:288`)이 이미 임시 착수 + `try/finally` 복원 패턴을
  쓰고 있다. 착수 전 판정도 같은 패턴을 재사용한다.
- `GomokuGame.validate_move()`(`game.py:261`)는 상태를 바꾸지 않고 검증만 하며, 실패 시
  `GameError`를 던지고 보드를 건드리지 않는다. 금수 검사를 이 체인의 **마지막 단계**로
  넣으면 "거부해도 보드·차례 불변"이 구조적으로 보장된다.
- `GomokuGame.last_move`(`game.py:140`)는 이미 추적 중이므로 `game_state`에 실어 보내면
  된다.
- 오셀로가 이미 "매 착수 후 authoritative `game_state` 재전송" 패턴을 쓴다
  (`main.py:383`). 오목도 같은 패턴을 따르면 금수 목록 동기화가 공짜로 해결된다.

## 3. 결정 대기 항목

아래 6개는 구현 착수 전에 확정한다. 나머지 절은 **권고안 기준**으로 작성되어 있으므로,
다른 선택을 하면 해당 절을 함께 수정한다.

| # | 논점 | 권고안 | 대안 |
| --- | --- | --- | --- |
| D1 | 제약 플레이어의 모든 빈 자리가 금수인 교착 상태 | 제약 플레이어 패배, `reason: "no_forbidden_free_move"` | 무승부 / 해당 라운드에 한해 금수 해제 |
| D2 | 금수 목록 전달 채널 | `game_state`에 싣고, 오목도 매 착수 후 `game_state` 재전송 | `move_result`에 `forbidden_moves` 추가 (payload 절약) |
| D3 | 금수 목록 계산 대상 | 차례와 무관하게 항상 제약 색 기준으로 계산 | 제약 플레이어 차례일 때만 계산 |
| D4 | 거부 메시지 형식 | 기존 `error` 프레임에 `forbidden_type`/`x`/`y` 추가 | 신규 `move_rejected` 메시지 타입 |
| D5 | `game_state.forbidden_type` | 제거 (클라이언트를 같은 릴리스에서 갱신) | `null` 고정으로 1릴리스 유지 후 제거 |
| D6 | 전수 스캔 prefilter | 도입 (§6.3, 3~5배 단축) | 미도입 (최대 16ms 그대로 수용) |

D1은 실전에서 사실상 도달하지 않는 상태지만 서버는 결정적으로 동작해야 하므로 반드시
정한다. 권고안은 "제약 플레이어는 반드시 착수해야 하는데 합법 착수가 없다"는 렌주 본래의
결말을 보존한다 — 실수로 금수를 클릭해서 지는 문제만 없애고, 진짜 막힌 국면의 승패는
그대로 둔다.

## 4. 와이어 계약

### 4.1 금수 착수 거부

금수 자리에 `move`가 오면 해당 연결에만 다음을 보낸다. 보드, 차례, `move_count`,
`last_move` 어느 것도 변하지 않고 상대에게는 아무 메시지도 가지 않는다.

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

| 필드 | 설명 |
| --- | --- |
| `code` | 신규 오류 코드 `FORBIDDEN_MOVE` |
| `forbidden_type` | `DOUBLE_THREE` / `DOUBLE_FOUR` / `OVERLINE` |
| `x`, `y` | 거부된 좌표. 클라이언트가 해당 점을 강조하는 데 쓴다 |
| `message` | 그대로 표시 가능한 기본 영문 안내 문구 |

`type`은 기존 `error`를 유지한다(D4). 이미 모든 클라이언트가 `error`를 처리하고 있으므로
새 코드를 모르는 클라이언트도 최소한 문구는 표시한다.

### 4.2 금수 목록 (`game_state`)

오목 `game_state`에 세 필드를 추가하고 `forbidden_type`을 제거한다.

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
    { "x": 3, "y": 4, "forbidden_type": "DOUBLE_THREE" },
    { "x": 9, "y": 6, "forbidden_type": "DOUBLE_FOUR" }
  ]
}
```

| 필드 | 게임 종류 | 값 |
| --- | --- | --- |
| `constrained_color` | GOMOKU만 | 렌주 금수가 적용되는 색. 자유룰이면 `null` |
| `forbidden_moves` | GOMOKU만 | `constrained_color` 기준 금수 좌표 목록 |
| `last_move` | 공통 | 마지막 착수 좌표 객체 또는 `null` |

규칙:

- `forbidden_moves`는 **차례와 무관하게 항상 `constrained_color` 기준**으로 계산한다(D3).
  두 클라이언트가 같은 목록을 받으므로 화면 상태가 어긋날 수 없다.
- `status`가 `FINISHED`이거나 `WAITING`이면 항상 `[]`이다.
- `FreeStyleRule`이면 항상 `[]`이고 `constrained_color`는 `null`이다.
- 오셀로 `game_state`에는 두 필드 모두 **넣지 않는다** (`score`/`legal_moves`가 오목에
  없는 것과 대칭).
- 정렬은 `(y, x)` 오름차순으로 고정한다. 테스트와 로그 비교를 결정적으로 만들기 위함이며
  클라이언트는 순서에 의존해서는 안 된다.

`last_move`는 이번 변경으로 새로 필요해진 필드다. 오목도 매 착수 후 `game_state`를 보내게
되는데(§4.4), `game_state`가 마지막 착수 정보를 담지 않으면 클라이언트의 최근 착수 표시가
매 수마다 지워진다. 오셀로도 같은 문제를 이미 갖고 있으므로 두 게임 모두에 넣는다.

### 4.3 `game_over` 변경

- `reason: "forbidden_move"`와 그에 딸린 `forbidden_type`, `x`, `y`는 **더 이상 발생하지
  않으며 전송하지 않는다**.
- 교착 상태(D1)에서만 새 사유가 나온다.

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

| `reason` | 의미 |
| --- | --- |
| `five_in_a_row` | 승리 (변경 없음) |
| `draw` | 보드 만석 무승부 (변경 없음) |
| `no_forbidden_free_move` | 제약 플레이어의 빈 자리가 전부 금수 (신규) |
| ~~`forbidden_move`~~ | 제거 |

### 4.4 메시지 순서

오목도 오셀로와 동일하게 **매 착수 후** authoritative `game_state`를 브로드캐스트한다(D2).

```text
move (합법)   → move_result → game_state → [game_over]
move (금수)   → error(FORBIDDEN_MOVE)              (요청자에게만)
```

15x15 보드의 `game_state` JSON은 약 1.5KB, 19x19는 약 2.4KB다. 2인 게임에서 무시할 수 있는
비용이며, 클라이언트가 금수 목록을 자체 계산할 필요가 없어진다.

## 5. 규칙 엔진 변경 (`app/rules.py`)

### 5.1 `RuleVerdict` 축소

```python
@dataclass(frozen=True)
class RuleVerdict:
    """Result of judging one placed stone."""

    win: bool = False
```

`forbidden` 필드와 `is_forbidden` 프로퍼티를 제거한다. 금수는 착수 *전에* 판정되므로
"놓인 돌을 판정한 결과"에 금수가 섞일 여지를 남기지 않는다.

### 5.2 `Rule` 기본 API

```python
class Rule(ABC):
    def constrained_color(self, starting_color: Color) -> Optional[Color]:
        """금수 제약을 받는 색. 제약이 없는 룰이면 ``None``."""
        return None

    def classify_forbidden(
        self, board: Board, x: int, y: int, color: Color, starting_color: Color
    ) -> Optional[ForbiddenType]:
        """``(x, y)``에 ``color``를 두면 금수인지 **착수 전에** 판정한다.

        ``(x, y)``는 비어 있어야 한다. 금수가 아니면 ``None``.
        보드는 영구적으로 변경되지 않는다.
        """
        return None

    def forbidden_points(
        self, board: Board, starting_color: Color
    ) -> dict[tuple[int, int], ForbiddenType]:
        """현재 국면에서 제약 색이 둘 수 없는 모든 빈 점."""
        return {}
```

`FreeStyleRule`은 세 메서드를 모두 상속만 하고 재정의하지 않는다. 자유룰 방에서는 스캔
자체가 실행되지 않는다.

### 5.3 `RenjuRule` 구현

```python
class RenjuRule(Rule):
    def constrained_color(self, starting_color: Color) -> Optional[Color]:
        return starting_color

    def evaluate_move(self, board, x, y, color, starting_color) -> RuleVerdict:
        if color is not starting_color:
            return RuleVerdict(win=self.longest_run(board, x, y, color) >= self.win_length)
        # 장목은 착수 단계에서 막히므로 제약 색의 줄은 win_length를 넘을 수 없다.
        return RuleVerdict(win=self.check_exact_five(board, x, y, color))

    def classify_forbidden(self, board, x, y, color, starting_color):
        if color is not starting_color or board[y][x] is not None:
            return None
        board[y][x] = color  # temporary probe, always undone below
        try:
            return self._classify_placed(board, x, y, color)
        finally:
            board[y][x] = None

    def _classify_placed(self, board, x, y, color, depth: int = 0):
        """이미 놓인 돌의 금수 종류. 판정 순서는 기존 ``is_forbidden``과 동일하다."""
        if self.check_overline(board, x, y, color):
            return ForbiddenType.OVERLINE
        if self.check_exact_five(board, x, y, color):
            # 5목을 만드는 수는 4-4/3-3로 처벌하지 않는다.
            return None
        if self.check_double_four(board, x, y, color):
            return ForbiddenType.DOUBLE_FOUR
        if self.check_double_three(board, x, y, color, depth):
            return ForbiddenType.DOUBLE_THREE
        return None

    def is_forbidden(self, board, x, y, color, depth: int = 0) -> bool:
        """재귀 판정용 bool 래퍼 (``_completion_is_forbidden``이 사용)."""
        return self._classify_placed(board, x, y, color, depth) is not None
```

요구 사항:

- `is_forbidden`의 **시그니처와 동작을 바꾸지 않는다**. `_completion_is_forbidden`
  (`rules.py:288`)이 이 메서드로 재귀하며, `MAX_JUDGE_DEPTH` 가드도 그대로다.
- `_classify_placed`는 `depth`를 받아 `check_double_three`에 넘긴다. 기존 `is_forbidden`이
  하던 일과 정확히 같다.
- `classify_forbidden`은 반드시 `try/finally`로 프로브를 되돌린다. 중간에 예외가 나도
  보드에 유령 돌이 남으면 안 된다.

### 5.4 `forbidden_points` 전수 스캔

```python
    def forbidden_points(self, board, starting_color):
        color = starting_color
        board_size = len(board)
        points: dict[tuple[int, int], ForbiddenType] = {}
        for y in range(board_size):
            for x in range(board_size):
                if board[y][x] is not None:
                    continue
                if not self._may_be_forbidden(board, x, y, color):
                    continue
                board[y][x] = color
                try:
                    kind = self._classify_placed(board, x, y, color)
                finally:
                    board[y][x] = None
                if kind is not None:
                    points[(x, y)] = kind
        return points
```

## 6. 성능

### 6.1 실측

`server/.venv`의 Python으로 `is_forbidden` 전수 스캔을 측정한 결과다. 중앙에 몰린 무작위
국면 기준, 1회 스캔당 시간이다.

| 보드 | 착수된 돌 | prefilter 없음 | prefilter 적용 | 실제 프로브 수 |
| --- | --- | --- | --- | --- |
| 15x15 | 20 | 10.4 ms | 2.1 ms | 205 → 16 |
| 15x15 | 60 | 7.1 ms | 2.9 ms | 165 → 51 |
| 19x19 | 40 | 15.6 ms | 3.4 ms | 321 → 41 |
| 19x19 | 120 | 10.8 ms | 4.5 ms | 241 → 78 |
| 19x19 | 240 | 5.0 ms | 3.0 ms | 121 → 67 |

두 방식의 판정 결과는 모든 표본에서 동일했다.

### 6.2 수용 가능성

스캔은 room lock 안에서 동기적으로 실행되므로 그동안 이벤트 루프가 막힌다. 최악 16ms는
2인 게임 서버에서 수용 가능하지만, 동시 진행 방이 많아지면 누적된다. prefilter 도입을
권고한다(D6).

빈 보드에서는 프로브가 0개이므로 `reset()` 직후 스캔은 사실상 무료다.

### 6.3 Prefilter

임의의 빈 점 `(x, y)`가 제약 색에게 금수가 되려면, 4개 축 위 반경 `win_length - 1` 안에
**같은 색 돌이 최소 4개** 있어야 한다.

```python
#: 금수 패턴은 이 반경 안에서 완결된다.
_PREFILTER_RADIUS_OFFSET = 1     # radius = win_length - 1

#: 가장 값싼 금수(3-3)조차 두 축에 각각 자기 돌 2개를 요구한다.
_PREFILTER_MIN_STONES = 4

#: win_length가 5보다 작으면 아래 하한 논증이 성립하지 않으므로 prefilter를 끈다.
_PREFILTER_MIN_WIN_LENGTH = 5
```

하한 논증:

| 금수 | 필요한 자기 돌 (놓을 돌 제외) | 최대 거리 |
| --- | --- | --- |
| 3-3 | 두 축 × 2개 = **4개** | 3 |
| 4-4 | 두 축 × 3개 = 6개 | 4 |
| 장목 | 한 축 5개 이상 | 5 (반경 4 안에 최소 4개) |

중심점을 지나는 4개 축은 중심을 제외하면 서로 겹치지 않으므로 중복 계수가 없다. 따라서
"반경 4 안에 자기 돌 4개 미만"이면 어떤 금수도 성립할 수 없고, 프로브를 건너뛰어도 안전하다.

```python
    def _may_be_forbidden(self, board, x, y, color) -> bool:
        if self.win_length < _PREFILTER_MIN_WIN_LENGTH:
            return True
        board_size = len(board)
        radius = self.win_length - _PREFILTER_RADIUS_OFFSET
        found = 0
        for dx, dy in DIRECTIONS:
            for step in range(-radius, radius + 1):
                if step == 0:
                    continue
                cx, cy = x + dx * step, y + dy * step
                if in_bounds(board_size, cx, cy) and board[cy][cx] is color:
                    found += 1
                    if found >= _PREFILTER_MIN_STONES:
                        return True
        return False
```

prefilter는 **성능 최적화일 뿐 규칙이 아니다**. §9의 동치성 테스트로 `_may_be_forbidden`
없이 돌린 결과와 항상 일치함을 강제한다.

## 7. 게임 계층 변경 (`app/game.py`)

### 7.1 종료 사유

```python
class GameOverReason(str, Enum):
    FIVE_IN_A_ROW = "five_in_a_row"
    DRAW = "draw"
    NO_LEGAL_MOVES = "no_legal_moves"          # 오셀로
    BOARD_FULL = "board_full"                  # 오셀로
    NO_FORBIDDEN_FREE_MOVE = "no_forbidden_free_move"   # 신규
```

`FORBIDDEN_MOVE`를 제거한다.

### 7.2 `MoveResult`

`forbidden_type` 필드와 `is_forbidden` 프로퍼티를 제거한다. `MoveResult`는 이제 "수락된
착수"만 표현하며, 금수는 결과가 아니라 예외다.

### 7.3 `GomokuGame` 상태

```python
        self.forbidden_moves: dict[tuple[int, int], ForbiddenType] = {}
```

`self.forbidden_type` 필드를 제거하고 위 필드를 추가한다. 이 딕셔너리는 **현재 국면에서
제약 색이 둘 수 없는 점 전체**이며, `__init__`, `reset`, 그리고 수락된 매 착수 후에
갱신된다.

```python
    @property
    def constrained_color(self) -> Optional[Color]:
        return self.rule.constrained_color(self.starting_color)

    def _refresh_forbidden_moves(self) -> None:
        self.forbidden_moves = self.rule.forbidden_points(self.board, self.starting_color)

    @property
    def _empty_count(self) -> int:
        return self.intersection_count - self.move_count
```

`is_constrained(color)`(`game.py:238`)는 `color is self.constrained_color`로 바꾼다.
자유룰에서 `starting_color`가 제약을 받는 것처럼 보이던 버그성 동작이 사라진다.

`_finish()`는 `forbidden_type` 인자를 잃고, 대신 `self.forbidden_moves = {}`를 수행한다.
끝난 게임에는 차례가 없듯 금수 목록도 없다.

### 7.4 착수 전 금수 검증

`validate_move()`(`game.py:261`)의 **마지막 단계**로 추가한다. `POSITION_OCCUPIED` 검사
뒤여야 한다 — 이미 돌이 있는 자리에 금수 판정을 시도하면 안 된다.

```python
        if self.board[y][x] is not None:
            raise PositionOccupiedError()
        forbidden = self.forbidden_moves.get((x, y))
        if forbidden is not None and color is self.constrained_color:
            raise ForbiddenMoveError(forbidden, x, y)
```

캐시된 `forbidden_moves`를 그대로 조회한다. 이 딕셔너리는 현재 국면에 대해 항상 최신이므로
착수 시점에 다시 프로브할 필요가 없고, 검증이 O(1)이 된다.

> 방어적으로 재계산하고 싶다면 `self.rule.classify_forbidden(...)`을 직접 호출해도 결과는
> 같다. 다만 캐시와 재계산이 갈리면 `forbidden_moves`가 신뢰할 수 없다는 뜻이므로,
> 그 경우는 §9의 불변식 테스트로 잡는다.

### 7.5 `make_move` 흐름

```python
        verdict = self.rule.evaluate_move(self.board, x, y, color, self.starting_color)
        if verdict.win:
            self._finish(winner=color, loser=color.opponent,
                         reason=GameOverReason.FIVE_IN_A_ROW)
        elif self.is_board_full():
            self._finish(winner=None, loser=None, reason=GameOverReason.DRAW)
        else:
            self.current_turn = color.opponent
            self._refresh_forbidden_moves()
            if self._is_stuck(self.current_turn):
                self._finish(
                    winner=self.current_turn.opponent,
                    loser=self.current_turn,
                    reason=GameOverReason.NO_FORBIDDEN_FREE_MOVE,
                )
```

```python
    def _is_stuck(self, color: Optional[Color]) -> bool:
        """제약 색의 차례인데 빈 자리가 전부 금수인가 (D1)."""
        return (
            color is not None
            and color is self.constrained_color
            and len(self.forbidden_moves) >= self._empty_count
        )
```

교착 판정이 금수 목록 갱신의 부산물이라 추가 비용이 없다.

`reset()`은 `self.forbidden_moves = {}` 후 `_refresh_forbidden_moves()`를 호출한다. 빈
보드에서는 결과가 `{}`지만, "언제나 현재 국면과 일치한다"는 불변식을 코드로 못박는다.

### 7.6 신규 예외 (`app/errors.py`)

```python
class ForbiddenMoveError(GameError):
    code = "FORBIDDEN_MOVE"
    default_message = "This point is forbidden for the first player."

    def __init__(
        self,
        forbidden_type: "ForbiddenType",
        x: int,
        y: int,
        message: Optional[str] = None,
    ) -> None:
        self.forbidden_type = forbidden_type
        self.x = x
        self.y = y
        super().__init__(message)
```

`ForbiddenType` import는 순환을 만들지 않는다 (`errors` → `rules` 방향 의존은 없어야
하므로 `TYPE_CHECKING` 블록에서만 import하고 런타임에는 타입을 강제하지 않는다).

## 8. 프로토콜·네트워크 계층

### 8.1 `app/protocol.py`

- `ErrorCode`에 `FORBIDDEN_MOVE = "FORBIDDEN_MOVE"` 추가.
- `FORBIDDEN_LABELS`(`protocol.py:76`)는 유지하되 용도가 "패배 문구"에서 "거부 문구"로
  바뀐다.
- 신규 빌더:

```python
def forbidden_move_error(color: Color, exc: ForbiddenMoveError) -> dict[str, Any]:
    label = FORBIDDEN_LABELS.get(exc.forbidden_type, "forbidden")
    return {
        "type": ServerMessageType.ERROR.value,
        "code": ErrorCode.FORBIDDEN_MOVE.value,
        "message": f"{label.capitalize()} is forbidden for {color.value}.",
        "forbidden_type": exc.forbidden_type.value,
        "x": exc.x,
        "y": exc.y,
    }
```

- `describe_game_over`: 금수 분기를 제거하고 교착 분기를 추가한다.

```python
    if reason is GameOverReason.NO_FORBIDDEN_FREE_MOVE and loser is not None:
        return f"{loser.value} has no playable point left."
```

- `game_over`: `forbidden_type`/`x`/`y`를 넣던 블록(`protocol.py:372-374`)을 제거한다.
- `game_state`: `forbidden_type` 키를 제거하고 다음을 추가한다.

```python
    last_move = game.last_move
    payload["last_move"] = (
        {"x": last_move[0], "y": last_move[1]} if last_move else None
    )
    if game.game_type is GameType.GOMOKU:
        constrained = game.constrained_color
        payload["constrained_color"] = constrained.value if constrained else None
        payload["forbidden_moves"] = [
            {"x": x, "y": y, "forbidden_type": kind.value}
            for (x, y), kind in sorted(game.forbidden_moves.items(), key=lambda i: (i[0][1], i[0][0]))
        ]
```

`last_move`는 두 게임 종류 공통이다. 오셀로 분기(`score`/`legal_moves`)는 그대로 둔다.

### 8.2 `app/main.py`

`_handle_move`(`main.py:346`)에서 `ForbiddenMoveError`를 **일반 `GameError`보다 먼저**
잡는다. `ForbiddenMoveError`는 `GameError`의 하위 클래스이므로 순서가 뒤집히면 상세 필드가
사라진다.

```python
    try:
        result = await room.make_move(session.connection_id, command.x, command.y)
    except ForbiddenMoveError as exc:
        color = room.color_of(session.connection_id)
        logger.info(
            "[ROOM %s] %s FORBIDDEN %s at (%d, %d) rejected",
            room.room_id, _color_label(room, session),
            exc.forbidden_type.value, exc.x, exc.y,
        )
        await gateway.manager.send(
            session, protocol.forbidden_move_error(color or room.starting_color, exc)
        )
        return
    except GameError as exc:
        ...
```

착수 후 `game_state` 브로드캐스트 조건(`main.py:383`)에서 게임 종류 분기를 제거한다.

```python
    await gateway.manager.broadcast_to_room(room, protocol.move_result(result))
    # 오목의 금수 목록과 오셀로의 뒤집기/합법 수 모두 authoritative snapshot으로 동기화한다.
    await gateway.manager.broadcast_to_room(room, protocol.game_state(room.game))
```

금수 패배 로그 블록(`main.py:387-393`)을 제거하고, 교착 종료 로그를 추가한다.

```python
        if result.reason is GameOverReason.NO_FORBIDDEN_FREE_MOVE:
            logger.info("[ROOM %s] %s STUCK (all points forbidden)",
                        room.room_id, result.loser.value)
```

### 8.3 `app/othello.py`

`self.forbidden_type = None`(`othello.py:41`, `othello.py:95`)을 제거한다. 프로토콜이 더
이상 이 속성을 읽지 않는다.

## 9. 테스트 지시

기존 테스트 중 아래는 **의미가 뒤집히므로 재작성**한다.

| 파일 | 대상 | 변경 |
| --- | --- | --- |
| `tests/test_renju.py:91-116, 136-188` | `verdict.forbidden` 단언 | `classify_forbidden` 반환값 단언으로 전환 |
| `tests/test_renju.py:226` | `test_is_forbidden_helper` | 유지 (재귀 경로 보호) |
| `tests/test_game.py:247-310` | 금수 패배 시나리오 | `ForbiddenMoveError` 발생 + 상태 불변 단언 |
| `tests/test_protocol.py:251-273, 323-334, 384-395` | 금수 `game_over`/`game_state` | 삭제 후 신규 계약 테스트로 대체 |
| `tests/test_room.py:71, 296-302, 377-379, 429-436` | `play_white_forbidden_loss` | 거부 후 게임 계속 헬퍼로 전환 |
| `tests/test_websocket.py:37, 685-697, 773` | `WHITE_FORBIDDEN_MOVES` 시나리오 | `error` 수신 + 차례 유지 시나리오로 전환 |

신규 테스트:

**규칙 (`tests/test_renju.py`)**

1. `classify_forbidden`이 3-3 / 4-4 / 장목에 대해 각각 정확한 `ForbiddenType`을 반환한다.
2. `classify_forbidden` 호출 후 보드가 **완전히 원상 복구**된다 (셀 단위 비교).
3. 5목을 만드는 수는 그것이 동시에 4-4여도 `None`을 반환한다.
4. 제약 색이 아닌 색에는 항상 `None`을 반환한다.
5. `FreeStyleRule.classify_forbidden` / `forbidden_points`가 항상 비어 있다.
6. **동치성**: 무작위 국면(시드 고정, 15x15와 19x19 각 20개)에서 `forbidden_points()`의
   결과가 `_may_be_forbidden`을 우회한 전수 스캔과 완전히 일치한다.
7. `forbidden_points`가 이미 돌이 놓인 점을 포함하지 않는다.

**게임 (`tests/test_game.py`)**

8. 금수 자리 착수 → `ForbiddenMoveError`. `board`, `move_count`, `current_turn`,
   `last_move`가 호출 전과 동일하다.
9. 예외의 `forbidden_type`, `x`, `y`가 정확하다.
10. 거부 직후 같은 플레이어가 다른 합법 자리에 두면 정상 수락된다.
11. 비제약 플레이어는 3-3/4-4/장목 자리에 자유롭게 둘 수 있다.
12. 자유룰 게임에서는 어떤 자리도 거부되지 않는다.
13. `forbidden_moves`가 매 수락 착수 후 갱신되고, 항상 빈 점만 담는다.
14. 종료 후 `forbidden_moves`가 `{}`이다.
15. 교착 국면(인위적으로 구성)에서 `NO_FORBIDDEN_FREE_MOVE`로 종료되고 제약 색이 패배한다.
16. `validate_move`의 검사 순서: 종료된 게임 > 미시작 > 차례 > 정수 > 범위 > 점유 > 금수.
    금수 자리가 이미 점유되어 있으면 `POSITION_OCCUPIED`가 나온다.

**프로토콜 (`tests/test_protocol.py`)**

17. `forbidden_move_error()` payload 전체 형태.
18. 오목 `game_state`에 `constrained_color`, `forbidden_moves`, `last_move`가 있고
    `forbidden_type`이 **없다**.
19. `forbidden_moves`가 `(y, x)` 순으로 정렬되어 있다.
20. 오셀로 `game_state`에 `constrained_color`/`forbidden_moves`가 **없고** `last_move`는 있다.
21. `describe_game_over(NO_FORBIDDEN_FREE_MOVE)` 문구.
22. `GameOverReason`에 `forbidden_move` 값이 존재하지 않는다.

**Room / WebSocket (`tests/test_room.py`, `tests/test_websocket.py`)**

23. 금수 `move` → 요청자만 `error(FORBIDDEN_MOVE)`를 받고 상대는 아무 프레임도 받지 않는다.
24. 거부 후 `current_turn`이 그대로이고, 같은 플레이어의 다음 합법 착수가 성공한다.
25. 매 오목 착수마다 `move_result` 다음에 `game_state`가 온다.
26. Lobby의 `room_list`가 금수 거부로 `FINISHED`가 되지 않는다.
27. Restart 색 배정이 영향을 받지 않는다 (5목 승패 기준으로만 동작).

## 10. 문서 갱신

`server/README.md`에서 다음을 고친다.

- 개요의 "WHITE 선공과 렌주 금수" → 금수가 착수 금지임을 명시.
- 오류 코드 표에 `FORBIDDEN_MOVE` 행 추가.
- `game_over` 절의 금수 패배 예시 삭제, `no_forbidden_free_move` 예시 추가.
- `reason` / `forbidden_type` 필드 표 갱신.
- `game_state` 예시에 `last_move`, `constrained_color`, `forbidden_moves` 추가 및
  `forbidden_type` 삭제.
- "게임 규칙 > 오목"의 "금수 착수는 즉시 패배" 항목 교체.
- 클라이언트 준수 사항에 "금수 표시는 `game_state.forbidden_moves`만 신뢰한다" 추가.
- 문서 목록에 이 문서와 클라이언트 문서 링크 추가.

## 11. 수용 기준

- [ ] 제약 플레이어가 3-3 / 4-4 / 장목 자리를 클릭하면 `error(FORBIDDEN_MOVE)`만 오고
      보드·차례·`move_count`가 변하지 않는다.
- [ ] 같은 자리를 반복 클릭해도 서버 상태가 변하지 않고 게임이 끝나지 않는다.
- [ ] 5목을 만드는 수가 동시에 4-4여도 승리로 처리된다.
- [ ] 제약 플레이어가 6목 이상을 만들 수 있는 국면이 존재하지 않는다.
- [ ] 비제약 플레이어는 어떤 자리에도 둘 수 있다.
- [ ] `game_state.forbidden_moves`가 항상 현재 국면의 금수 집합과 정확히 일치한다.
- [ ] 오목 착수마다 `move_result` → `game_state` 순으로 브로드캐스트된다.
- [ ] `forbidden_move` 종료 사유가 코드와 와이어 어디에도 남아 있지 않다.
- [ ] 19x19 만원 직전 국면에서도 착수당 금수 스캔이 20ms를 넘지 않는다.
- [ ] `classify_forbidden` / `forbidden_points` 호출 전후로 보드가 비트 단위로 동일하다.
