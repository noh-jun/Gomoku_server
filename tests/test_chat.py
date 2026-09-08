"""Unit tests for room chat: text rules, wire parsing and the broadcast frame."""

from __future__ import annotations

import json

import pytest

from app.chat import MAX_CHAT_TEXT_LENGTH, normalize_chat_text
from app.errors import ChatTextInvalidError
from app.protocol import ChatCommand, ErrorCode, ProtocolError, chat_message, parse_client_message


class TestNormalizeChatText:
    def test_strips_surrounding_whitespace_only(self) -> None:
        assert normalize_chat_text("  한 수  더！  ") == "한 수  더！"

    def test_does_not_apply_nfkc(self) -> None:
        # Full-width exclamation stays as typed; nicknames would fold it.
        assert normalize_chat_text("！") == "！"

    @pytest.mark.parametrize("value", ["", "   ", None, 42, ["x"]])
    def test_rejects_empty_and_non_string(self, value: object) -> None:
        with pytest.raises(ChatTextInvalidError) as excinfo:
            normalize_chat_text(value)
        assert excinfo.value.code == "CHAT_TEXT_INVALID"

    def test_length_boundary(self) -> None:
        assert len(normalize_chat_text("가" * MAX_CHAT_TEXT_LENGTH)) == MAX_CHAT_TEXT_LENGTH
        with pytest.raises(ChatTextInvalidError):
            normalize_chat_text("가" * (MAX_CHAT_TEXT_LENGTH + 1))

    @pytest.mark.parametrize("value", ["첫줄\n둘째줄", "탭\t포함", "줄 구분", "\x07벨"])
    def test_rejects_control_and_line_separators(self, value: str) -> None:
        with pytest.raises(ChatTextInvalidError):
            normalize_chat_text(value)


class TestChatWire:
    def test_parse_chat_returns_normalized_command(self) -> None:
        command = parse_client_message(json.dumps({"type": "chat", "text": "  안녕하세요  "}))
        assert command == ChatCommand(text="안녕하세요")

    def test_parse_chat_without_text_is_a_protocol_error(self) -> None:
        with pytest.raises(ProtocolError) as excinfo:
            parse_client_message(json.dumps({"type": "chat"}))
        assert excinfo.value.code is ErrorCode.CHAT_TEXT_INVALID

    @pytest.mark.parametrize("text", ["", "x" * 201, "a\nb", 7])
    def test_parse_chat_with_invalid_text_maps_to_chat_text_invalid(self, text: object) -> None:
        with pytest.raises(ProtocolError) as excinfo:
            parse_client_message(json.dumps({"type": "chat", "text": text}))
        assert excinfo.value.code is ErrorCode.CHAT_TEXT_INVALID

    def test_chat_message_frame_matches_client_contract(self) -> None:
        frame = chat_message("홍길동", "안녕하세요", 1_725_760_000_000)
        assert frame == {
            "type": "chat_message",
            "nickname": "홍길동",
            "text": "안녕하세요",
            "sent_at_unix_ms": 1_725_760_000_000,
        }
        assert set(frame) == {"type", "nickname", "text", "sent_at_unix_ms"}
