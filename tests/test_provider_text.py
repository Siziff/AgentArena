import unittest

from agentarena.agent.provider import (
    LLMResponse,
    TextActionProvider,
    _parse_json_object,
    build_text_protocol_instructions,
    parse_action_lines,
)
from agentarena.agent.tools import TOOL_SPECS


class TestParseActionLines(unittest.TestCase):
    def test_parses_actions_and_reasoning(self):
        text = (
            "Thinking about what to do.\n"
            'ACTION list_dir {"path": "."}\n'
            'ACTION comment {"text": "Listing files."}\n'
            "More reasoning here.\n"
            "```\n"
            'ACTION finish {}\n'
            "```\n"
        )
        tool_calls, rest = parse_action_lines(text)
        self.assertEqual([t.name for t in tool_calls], ["list_dir", "comment", "finish"])
        self.assertEqual(tool_calls[0].arguments, {"path": "."})
        self.assertEqual(tool_calls[1].arguments, {"text": "Listing files."})
        self.assertIn("reasoning", rest)
        self.assertNotIn("ACTION", rest)

    def test_no_actions(self):
        tool_calls, rest = parse_action_lines("just some text")
        self.assertEqual(tool_calls, [])
        self.assertEqual(rest, "just some text")

    def test_parses_name_arguments_json_format(self):
        text = (
            '{"name": "run_shell", "arguments": {"command": "ls"}} '
            '{"name": "comment", "arguments": {"text": "did it"}}\n'
            "some reasoning"
        )
        tool_calls, rest = parse_action_lines(text)
        self.assertEqual([t.name for t in tool_calls], ["run_shell", "comment"])
        self.assertEqual(tool_calls[0].arguments, {"command": "ls"})
        self.assertEqual(tool_calls[1].arguments, {"text": "did it"})
        # the JSON tool objects are removed from the shown free text
        self.assertNotIn("run_shell", rest)
        self.assertIn("reasoning", rest)


class TestParseJsonObject(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(_parse_json_object('{"a": 1}'), {"a": 1})

    def test_with_surrounding_text(self):
        self.assertEqual(_parse_json_object('cmd {"a": 1} end'), {"a": 1})

    def test_empty_and_invalid(self):
        self.assertEqual(_parse_json_object(""), {})
        self.assertEqual(_parse_json_object("no json here"), {})


class TestProtocolInstructions(unittest.TestCase):
    def test_contains_all_tools(self):
        text = build_text_protocol_instructions(TOOL_SPECS)
        self.assertIn("ACTION <tool_name>", text)
        for spec in TOOL_SPECS:
            self.assertIn(spec.name, text)


class _FakeBase:
    """Minimal stand-in for an OpenAICompatibleProvider."""

    def __init__(self, content):
        self.content = content
        self.calls = []

    def complete(self, messages, tools):
        self.calls.append((list(messages), tools))
        return LLMResponse(content=self.content)


class TestTextActionProvider(unittest.TestCase):
    def test_wraps_and_parses(self):
        base = _FakeBase('ACTION comment {"text": "hi"}\nthinking out loud')
        provider = TextActionProvider(base)
        resp = provider.complete([], TOOL_SPECS)

        # parsed the ACTION line into a tool call
        self.assertEqual(len(resp.tool_calls), 1)
        self.assertEqual(resp.tool_calls[0].name, "comment")
        self.assertEqual(resp.tool_calls[0].arguments, {"text": "hi"})
        # remaining free text becomes content
        self.assertEqual(resp.content, "thinking out loud")

        # the base was called WITHOUT native tools, with a protocol system prompt
        sent_messages, sent_tools = base.calls[0]
        self.assertEqual(sent_tools, [])
        self.assertEqual(sent_messages[0].role, "system")
        self.assertIn("ACTION <tool_name>", sent_messages[0].content)

    def test_merges_into_existing_system_message(self):
        from agentarena.agent.provider import Message

        base = _FakeBase("no actions")
        provider = TextActionProvider(base)
        provider.complete([Message(role="system", content="ORIGINAL")], TOOL_SPECS)
        sent_messages, _ = base.calls[0]
        self.assertIn("ORIGINAL", sent_messages[0].content)
        self.assertIn("ACTION <tool_name>", sent_messages[0].content)


if __name__ == "__main__":
    unittest.main()
