"""Deterministic tests for the direct Apple Foundation Models provider POC."""

from __future__ import annotations

from types import SimpleNamespace
from typing import ClassVar

import pytest

from coworker.providers.apple_foundation_poc import (
    AppleFoundationModelsPOC,
    argument_schema,
    canonical_prompt,
    routing_schema,
)


class _Generated:
    def __init__(self, value):
        self.value = value

    def to_dict(self):
        return self.value


class _FakeModel:
    context_size = 4096

    def is_available(self):
        return True, None


class _FakeSession:
    scripted: ClassVar[list] = []
    instances: ClassVar[list] = []

    def __init__(self, *, model, instructions=None):
        self.model = model
        self.instructions = instructions
        self.calls = []
        self.__class__.instances.append(self)

    async def respond(self, prompt, json_schema=None):
        self.calls.append((prompt, json_schema))
        value = self.__class__.scripted.pop(0)
        return _Generated(value) if isinstance(value, dict) else value

    async def stream_response(self, prompt):
        self.calls.append((prompt, None))
        for value in self.__class__.scripted.pop(0):
            yield value


@pytest.fixture
def sdk():
    _FakeSession.scripted = []
    _FakeSession.instances = []
    return SimpleNamespace(
        SystemLanguageModel=_FakeModel,
        LanguageModelSession=_FakeSession,
    )


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read one file",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    }
]


def test_probe_reports_live_availability(sdk):
    assert AppleFoundationModelsPOC(sdk).probe() == (
        AppleFoundationModelsPOC(sdk).probe()
    )
    probe = AppleFoundationModelsPOC(sdk).probe()
    assert probe.available is True
    assert probe.context_size == 4096


def test_canonical_prompt_separates_system_and_strips_sidecars():
    instructions, prompt = canonical_prompt(
        [
            {"role": "system", "content": "Be exact"},
            {"role": "user", "content": "hello", "_gemini": {"signature": "secret"}},
            {"role": "tool", "name": "read_file", "content": "untrusted"},
        ]
    )
    assert instructions == "Be exact"
    assert "_gemini" not in prompt
    assert '"role":"user"' in prompt
    assert '"role":"tool"' in prompt
    assert prompt.count(":") > 2


def test_schema_adds_apple_ordering_metadata():
    route = routing_schema(TOOLS)
    args = argument_schema(TOOLS[0])
    assert route["x-order"] == ["kind", "text", "tool_name"]
    assert route["properties"]["tool_name"]["enum"] == ["read_file"]
    assert args["x-order"] == ["path"]
    assert args["additionalProperties"] is False


def test_complete_text_uses_real_provider_boundary(sdk):
    _FakeSession.scripted = ["hello"]
    turn = AppleFoundationModelsPOC(sdk).complete(
        model="system", messages=[{"role": "user", "content": "hi"}]
    )
    assert turn.text == "hello"
    assert turn.finish_reason == "stop"
    assert _FakeSession.instances[0].instructions is None


def test_guided_message_does_not_generate_arguments(sdk):
    _FakeSession.scripted = [
        {"kind": "message", "text": "No tool needed.", "tool_name": "read_file"}
    ]
    turn = AppleFoundationModelsPOC(sdk).complete(
        model="system",
        messages=[{"role": "user", "content": "say hello"}],
        tools=TOOLS,
    )
    assert turn.text == "No tool needed."
    assert not turn.tool_calls
    assert len(_FakeSession.instances[0].calls) == 1


def test_two_stage_guided_tool_proposal_maps_to_tool_call(sdk):
    _FakeSession.scripted = [
        {"kind": "tool", "text": "I need the file.", "tool_name": "read_file"},
        {"path": "README.md"},
    ]
    turn = AppleFoundationModelsPOC(sdk).complete(
        model="system",
        messages=[{"role": "user", "content": "read the README"}],
        tools=TOOLS,
    )
    assert turn.finish_reason == "tool_calls"
    assert turn.text == "I need the file."
    assert len(turn.tool_calls) == 1
    assert turn.tool_calls[0].name == "read_file"
    assert turn.tool_calls[0].arguments == {"path": "README.md"}
    assert turn.tool_calls[0].id.startswith("apple_")
    assert len(_FakeSession.instances[0].calls) == 2


def test_stream_converts_apple_snapshots_to_deltas(sdk):
    _FakeSession.scripted = [["H", "Hel", "Hello"]]
    chunks = list(
        AppleFoundationModelsPOC(sdk).stream(
            model="system", messages=[{"role": "user", "content": "hi"}]
        )
    )
    assert [chunk.text_delta for chunk in chunks[:-1]] == ["H", "el", "lo"]
    assert chunks[-1].turn.text == "Hello"


def test_cancel_closes_stream_without_final_turn(sdk):
    _FakeSession.scripted = [["Hello", "Hello world"]]
    provider = AppleFoundationModelsPOC(sdk)
    stream = provider.stream(
        model="system", messages=[{"role": "user", "content": "hi"}]
    )
    assert next(stream).text_delta == "Hello"
    provider.cancel()
    assert list(stream) == []


def test_invalid_tool_schema_fails_before_model_call(sdk):
    with pytest.raises(ValueError, match="function name"):
        AppleFoundationModelsPOC(sdk).complete(
            model="system",
            messages=[],
            tools=[{"type": "function", "function": {}}],
        )
