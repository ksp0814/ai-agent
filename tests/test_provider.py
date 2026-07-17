import unittest
from pathlib import Path
from unittest.mock import patch

from personal_agent.agent import CodexCliProvider, OpenAIProvider


class ProviderTests(unittest.TestCase):
    @patch("personal_agent.agent.subprocess.run")
    def test_codex_cli_provider_uses_existing_login(self, run):
        run.return_value.returncode = 0
        run.return_value.stdout = "Codex 응답"
        run.return_value.stderr = ""

        provider = CodexCliProvider(Path("/workspace"))
        self.assertEqual(provider.respond("파일을 설명해줘", ["Python 선호"]), "Codex 응답")
        command = run.call_args.args[0]
        self.assertEqual(command[:3], ["codex", "exec", "--ephemeral"])
        self.assertIn("--sandbox", command)
        self.assertIn("--skip-git-repo-check", command)
        self.assertIn("/workspace", command)

    @patch("personal_agent.agent.subprocess.run")
    def test_codex_cli_provider_passes_model_and_reasoning(self, run):
        run.return_value.returncode = 0
        run.return_value.stdout = "Codex 응답"
        run.return_value.stderr = ""

        provider = CodexCliProvider(Path("/workspace"), model="gpt-5.6-luna", reasoning_effort="high")
        provider.respond("요청", [])
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--model") + 1], "gpt-5.6-luna")
        self.assertIn('model_reasoning_effort="high"', command)

    def test_extracts_responses_text(self):
        body = {"output": [{"content": [{"type": "output_text", "text": "안녕하세요"}]}]}
        self.assertEqual(OpenAIProvider._extract_text(body), "안녕하세요")

    def test_extracts_convenience_text(self):
        self.assertEqual(OpenAIProvider._extract_text({"output_text": "완료"}), "완료")


if __name__ == "__main__":
    unittest.main()
