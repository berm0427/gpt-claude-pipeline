"""Local security regressions. Never call subscribed model CLIs."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pipeline


class PipelineSecurityTests(unittest.TestCase):
    def test_environment_does_not_inherit_credentials(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "not-for-child",
                                     "ANTHROPIC_AUTH_TOKEN": "not-for-child",
                                     "AWS_SECRET_ACCESS_KEY": "not-for-child"}):
            child = pipeline._subscription_env()
        for key in ("OPENAI_API_KEY", "ANTHROPIC_AUTH_TOKEN", "AWS_SECRET_ACCESS_KEY"):
            self.assertNotIn(key, child)

    def test_invalid_input_and_code_blocks_are_rejected(self):
        with self.assertRaises(pipeline.PipelineError):
            pipeline._validate_text("a\x00b", 100, "input")
        with self.assertRaises(pipeline.PipelineError):
            pipeline._validate_text("a" * 101, 100, "input")
        for bad in ("plain code", "```py\na\n```\n```py\nb\n```"):
            with self.assertRaises(pipeline.PipelineError):
                pipeline.extract_code(bad)
        self.assertEqual(pipeline.extract_code("Before\n```python\nx = 1\n```\nAfter"), "x = 1")

    def test_verdict_accepts_emphasis_only(self):
        for ok in ("## 판정\nPASS", "## 판정\n**FIX_NEEDED**", "##판정 \n`PASS`"):
            self.assertEqual(len(pipeline.VERDICT_RE.findall(ok)), 1, ok)
        for bad in ("## 판정\n통과", "판정: PASS", "## 판정\nPASS 입니다"):
            self.assertEqual(len(pipeline.VERDICT_RE.findall(bad)), 0, bad)

    def test_subprocess_output_limit(self):
        with tempfile.TemporaryDirectory() as workdir, patch.object(pipeline, "MAX_OUTPUT_BYTES", 1024):
            with self.assertRaisesRegex(pipeline.PipelineError, "출력"):
                pipeline._run_once([sys.executable, "-c", "print('x' * 5000)"], "input", workdir)

    def test_subprocess_timeout(self):
        with tempfile.TemporaryDirectory() as workdir, patch.object(pipeline, "TIMEOUT_SEC", 0.2):
            with self.assertRaisesRegex(pipeline.PipelineError, "초과"):
                pipeline._run_once([sys.executable, "-c", "import time; time.sleep(5)"], "input", workdir)

    def test_result_file_limit(self):
        with tempfile.TemporaryDirectory() as workdir, patch.object(pipeline, "MAX_OUTPUT_BYTES", 1024):
            target = Path(workdir) / "result.txt"
            code = "from pathlib import Path; import time; Path('result.txt').write_bytes(b'x'*5000); time.sleep(5)"
            with self.assertRaisesRegex(pipeline.PipelineError, "결과 파일"):
                pipeline._run_once([sys.executable, "-c", code], "input", workdir, target)

    def test_claude_uses_restricted_no_tool_mode(self):
        with tempfile.TemporaryDirectory() as workdir, \
             patch.object(pipeline, "_find_cli", return_value="claude"), \
             patch.object(pipeline, "_run", return_value="reply") as run:
            self.assertEqual(pipeline.ask_claude("role", "task", workdir), "reply")
            cmd = run.call_args.args[0]
            self.assertIn("--restricted", cmd)
            self.assertEqual(cmd[cmd.index("--tools") + 1], "")

    def test_claude_resumes_selected_session_and_returns_id(self):
        response = '{"result":"reply","session_id":"11111111-1111-4111-8111-111111111111","is_error":false}'
        with tempfile.TemporaryDirectory() as workdir, \
             patch.object(pipeline, "_find_cli", return_value="claude"), \
             patch.object(pipeline, "_run", return_value=response) as run:
            result, session_id = pipeline.ask_claude("role", "task", workdir, resume="my-session")
            self.assertEqual((result, session_id), ("reply", "11111111-1111-4111-8111-111111111111"))
            cmd = run.call_args.args[0]
            self.assertEqual(cmd[cmd.index("--resume") + 1], "my-session")
            self.assertEqual(cmd[cmd.index("--output-format") + 1], "json")
            self.assertEqual(run.call_args.kwargs["max_retries"], 1)

    def test_codex_resumes_selected_session_without_ephemeral(self):
        with tempfile.TemporaryDirectory() as workdir, \
             patch.object(pipeline, "_find_cli", return_value="codex"), \
             patch.object(pipeline, "_run", return_value="") as run:
            def write_result(*args, **kwargs):
                Path(args[4]).write_text("reply", encoding="utf-8")
                return ""
            run.side_effect = write_result
            self.assertEqual(pipeline.ask_gpt("role", "task", workdir, resume="my-session"), "reply")
            cmd = run.call_args.args[0]
            self.assertEqual(cmd[1:3], ["exec", "resume"])
            self.assertIn("my-session", cmd)
            self.assertNotIn("--ephemeral", cmd)
            self.assertEqual(run.call_args.kwargs["max_retries"], 1)

    def test_isolated_run_directories(self):
        with tempfile.TemporaryDirectory() as root:
            responses = ["```python\na = 1\n```", "```python\na = 2\n```\n## 리팩토링 포인트\n- 변경",
                         "## 판정\nPASS\n## 이슈\n- 없음", "```python\na = 3\n```\n## 반영/기각 내역\n- 없음"]
            with patch.object(pipeline, "ask_gpt", side_effect=[responses[0], responses[2]]), \
                 patch.object(pipeline, "ask_claude", side_effect=[responses[1], responses[3]]):
                pipeline.run_code_pipeline("safe requirement", root)
            runs = list(Path(root).iterdir())
            self.assertEqual(len(runs), 1)
            self.assertEqual((runs[0] / "final_code.txt").read_text(encoding="utf-8"), "a = 3")

    def test_existing_sessions_are_used_for_both_model_turns(self):
        with tempfile.TemporaryDirectory() as root:
            gpt = ["```python\na = 1\n```", "## 판정\nPASS\n## 이슈\n- 없음"]
            claude = [("```python\na = 2\n```", "session-id"),
                      ("```python\na = 3\n```", "session-id")]
            with patch.object(pipeline, "ask_gpt", side_effect=gpt) as ask_gpt, \
                 patch.object(pipeline, "ask_claude", side_effect=claude) as ask_claude:
                pipeline.run_code_pipeline("requirement", root,
                                           claude_session="my-session", claude_project_dir=root,
                                           codex_session="CODEX", codex_project_dir=root)
            # 초안만 기존 Codex 대화, 검증은 새 일회성 세션
            self.assertEqual([c.kwargs["resume"] for c in ask_gpt.call_args_list],
                             ["CODEX", None])
            self.assertNotEqual(ask_gpt.call_args_list[1].args[2], root)
            self.assertEqual([c.kwargs["resume"] for c in ask_claude.call_args_list],
                             ["my-session", "session-id"])


if __name__ == "__main__":
    unittest.main()
