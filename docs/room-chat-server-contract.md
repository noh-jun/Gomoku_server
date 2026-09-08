# Room 채팅 서버 계약

- 채팅은 Room 단위의 단일 채널이다. 같은 Room의 Player와 Observer 전원에게 전달하며 Lobby 채팅은 없다.
- Room 안의 인증된 연결만 `{ "type": "chat", "text": "..." }`를 전송할 수 있다.
- `text`는 앞뒤 공백을 제거한 뒤 1~200자여야 하며 제어문자와 줄 구분 문자를 포함할 수 없다. NFKC 정규화는 하지 않는다.
- 서버는 검증을 통과한 메시지를 **발신자를 포함한** Room 전원에게 `chat_message`로 방송한다. 발신자에게 별도 확인 응답은 보내지 않는다.

```json
{
  "type": "chat_message",
  "nickname": "홍길동",
  "text": "안녕하세요",
  "sent_at_unix_ms": 1725760000000
}
```

- `nickname`은 발신 연결의 계정 닉네임이다. 내부 `connection_id`와 `account_id`는 전송하지 않는다.
- `sent_at_unix_ms`는 서버가 메시지를 수락한 시각(UTC epoch, 밀리초)이다.
- 서버는 대화를 저장하지 않는다. 입장 시 과거 대화를 내려주지 않으며 Room 삭제와 함께 사라진다.
- 채팅은 게임 상태(`WAITING`/`PLAYING`/`FINISHED`), 역할, Undo 처리 여부와 무관하게 항상 받는다. Turn Timer에도 영향을 주지 않는다.
- 연결당 **1초 동안 최대 2건**을 받는다. 초과분은 버리고 오류만 돌려준다.

## 오류

| code | 상황 |
|---|---|
| `CHAT_NOT_AVAILABLE` | Room 밖에서 보냈거나 인증되지 않은 연결 |
| `CHAT_TEXT_INVALID` | `text` 누락, 문자열 아님, 길이·문자 규칙 위반 |
| `CHAT_RATE_LIMITED` | 1초 창에서 3건째 이상 |

오류는 기존 `error` 메시지(`code`, `message`)로 발신자에게만 보내며 연결은 유지한다. 오류가 난 메시지는 방송하지 않는다.
