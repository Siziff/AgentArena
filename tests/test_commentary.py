import unittest

from agentarena.agent.commentary import CommentaryEmitter
from agentarena.core.types import Side


class TestCommentaryCleaning(unittest.TestCase):
    def setUp(self):
        self.em = CommentaryEmitter(side=Side.ALPHA, path=None)

    def test_strips_json_objects_keeps_thought(self):
        self.em.emit('{"name": "run_shell", "arguments": {"command": "ls"}} I listed the files.')
        self.assertEqual(self.em.entries[-1].text, "I listed the files.")

    def test_skips_json_only_messages(self):
        result = self.em.emit('{"name": "run_shell", "arguments": {"command": "ls"}}')
        self.assertIsNone(result)
        self.assertEqual(len(self.em.entries), 0)

    def test_skips_action_lines(self):
        self.em.emit('ACTION run_shell {"command": "ls"}\nI checked the directory.')
        self.assertEqual(self.em.entries[-1].text, "I checked the directory.")

    def test_dedup_consecutive_identical(self):
        self.em.emit("I hardened the vault.")
        self.em.emit("I hardened the vault.")
        self.assertEqual(len(self.em.entries), 1)

    def test_length_capped(self):
        self.em.emit("word " * 400)
        self.assertLessEqual(len(self.em.entries[-1].text), 600)

    def test_strips_tool_call_tags_and_comment_prefix(self):
        self.em.emit("<tool_call>stuff</tool_call>COMMENT: I am reading the archive now.")
        self.assertEqual(self.em.entries[-1].text, "I am reading the archive now.")

    def test_strips_garbled_acao_prefix(self):
        self.em.emit("AÇÃO COMMENT I will try the password now.")
        self.assertEqual(self.em.entries[-1].text, "I will try the password now.")


if __name__ == "__main__":
    unittest.main()
