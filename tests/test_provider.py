import unittest

from personal_agent.agent import OpenAIProvider


class ProviderTests(unittest.TestCase):
    def test_extracts_responses_text(self):
        body = {"output": [{"content": [{"type": "output_text", "text": "안녕하세요"}]}]}
        self.assertEqual(OpenAIProvider._extract_text(body), "안녕하세요")

    def test_extracts_convenience_text(self):
        self.assertEqual(OpenAIProvider._extract_text({"output_text": "완료"}), "완료")


if __name__ == "__main__":
    unittest.main()
