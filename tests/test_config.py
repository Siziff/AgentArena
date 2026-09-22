import tempfile
import unittest

from agentarena.config import AppConfig, LLMSettings, load_config
from agentarena.core.types import Side

TOML = """
[llm]
provider = "openai_compatible"
base_url = "http://shared/v1"
model = "shared-model"
api_key = "sekret"
temperature = 0.3
max_tokens = 512

[llm.alpha]
model = "model-a"
base_url = "http://a/v1"

[llm.bravo]
model = "model-b"
"""


class TestPerSideConfig(unittest.TestCase):
    def test_shared_fallback_when_no_overrides(self):
        app = AppConfig(llm=LLMSettings(model="m"))
        self.assertIs(app.llm_for(Side.ALPHA), app.llm)
        self.assertIs(app.llm_for(Side.BRAVO), app.llm)

    def test_load_per_side_overrides(self):
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as fh:
            fh.write(TOML)
            path = fh.name
        app = load_config(path)

        a = app.llm_for(Side.ALPHA)
        self.assertEqual(a.model, "model-a")            # overridden
        self.assertEqual(a.base_url, "http://a/v1")     # overridden
        self.assertEqual(a.api_key, "sekret")           # inherited
        self.assertEqual(a.max_tokens, 512)             # inherited

        b = app.llm_for(Side.BRAVO)
        self.assertEqual(b.model, "model-b")            # overridden
        self.assertEqual(b.base_url, "http://shared/v1")  # inherited
        self.assertEqual(b.temperature, 0.3)            # inherited

        # the shared table itself is unchanged
        self.assertEqual(app.llm.model, "shared-model")

    def test_openai_text_provider_selected(self):
        from agentarena.agent.provider import TextActionProvider
        from agentarena.config import build_provider_factory

        app = AppConfig(
            llm=LLMSettings(provider="openai_text", base_url="http://x/v1", model="m")
        )
        provider = build_provider_factory(app)(Side.ALPHA, "battle")
        self.assertIsInstance(provider, TextActionProvider)


if __name__ == "__main__":
    unittest.main()
