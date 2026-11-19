"""Unit tests that the saved system prompt still formats and keeps its sections."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pyfiles"))

from system_prompt import MEMORY_CREATOR_PROMPT, SYSTEM_PROMPT_TEMPLATE


class SystemPromptTests(unittest.TestCase):
    def test_placeholders_are_filled(self):
        text = SYSTEM_PROMPT_TEMPLATE.format(
            reply_language="Hindi, written in Devanagari.",
            user_memory="Omkar lives in Chennai.",
        )
        self.assertIn("Hindi, written in Devanagari.", text)
        self.assertIn("Omkar lives in Chennai.", text)
        self.assertNotIn("{reply_language}", text)
        self.assertNotIn("{user_memory}", text)

    def test_saved_sections_remain(self):
        for heading in (
            "# 1. Core principles",
            "# 2. How to think before you answer",
            "# 3. Accuracy and honesty rules",
            "# 4. Using tools",
            "# 5. Response style",
            "# 6. Domain guidance",
            "# 7. Safety and boundaries",
            "# 8. Handling ambiguity and long tasks",
            "# 9. Personality",
            "# 10. Final check before every response",
            "# SECURITY AND INSTRUCTION INTEGRITY (highest priority)",
            "Wikipedia search",
            "Age calculator",
            "Expense tools",
            "write the solution as a worked example",
            'Finish with a line "Result:"',
            'Then a line "Check:"',
        ):
            self.assertIn(heading, SYSTEM_PROMPT_TEMPLATE)

    def test_memory_extractor_asks_for_stable_facts_only(self):
        self.assertIn("should_write=false", MEMORY_CREATOR_PROMPT)
        self.assertIn("Don't store transient info.", MEMORY_CREATOR_PROMPT)


if __name__ == "__main__":
    unittest.main()
