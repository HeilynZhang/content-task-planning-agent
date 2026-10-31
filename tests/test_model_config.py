"""Offline configuration and provider-response checks, not real API evidence."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.model import ModelError, OpenAICompatibleModel, load_env_file

ROOT = Path(__file__).resolve().parents[1]


class ModelConfigTests(unittest.TestCase):
    def test_env_file_quotes_and_existing_environment_priority(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "local.env"
            path.write_text('# local only\nLLM_API_KEY="test-secret"\nLLM_MODEL=file-model\n', encoding="utf-8")
            with patch.dict(os.environ, {"LLM_MODEL": "process-model"}, clear=True):
                load_env_file(path)
                self.assertEqual(os.environ["LLM_API_KEY"], "test-secret")
                self.assertEqual(os.environ["LLM_MODEL"], "process-model")

    def test_invalid_config_does_not_echo_secret_or_partially_load(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "local.env"
            path.write_text("LLM_API_KEY=test-secret\nUNSUPPORTED=test-secret\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(ModelError) as error:
                    load_env_file(path)
                self.assertNotIn("test-secret", str(error.exception))
                self.assertNotIn("LLM_API_KEY", os.environ)

    @staticmethod
    def response(content, finish_reason="stop"):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            "id": "fixture-response", "model": "fixture-provider-model", "usage": {"total_tokens": 20},
            "choices": [{"finish_reason": finish_reason,
                         "message": {"content": content, "reasoning_content": "private-reasoning-fixture"}}]
        }).encode()
        return response

    def test_optional_thinking_parameter_and_no_reasoning_record(self):
        action = {"type": "finish", "status": "incomplete", "reason": "离线测试"}
        with patch.dict(os.environ, {"LLM_THINKING": "disabled"}, clear=True):
            model = OpenAICompatibleModel("https://example.invalid", "test-secret", "fixture")
            with patch("src.model.urllib.request.urlopen", return_value=self.response(json.dumps(action))) as transport:
                self.assertEqual(model.next_action({}), action)
        body = json.loads(transport.call_args.args[0].data)
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(model.metadata["response_metadata"][0]["finish_reason"], "stop")
        self.assertNotIn("private-reasoning-fixture", json.dumps(model.metadata))
        self.assertNotIn("test-secret", json.dumps(model.metadata))

    def test_empty_json_response_has_clear_error_and_usage(self):
        model = OpenAICompatibleModel("https://example.invalid", "test-secret", "fixture")
        with patch("src.model.urllib.request.urlopen", return_value=self.response("   ")):
            with self.assertRaisesRegex(ModelError, "空白"):
                model.next_action({})
        self.assertEqual(model.metadata["provider_usage"], [{"total_tokens": 20}])

    def test_truncated_response_has_clear_error(self):
        model = OpenAICompatibleModel("https://example.invalid", "test-secret", "fixture")
        with patch("src.model.urllib.request.urlopen", return_value=self.response('{"type":', "length")):
            with self.assertRaisesRegex(ModelError, "token 上限"):
                model.next_action({})

    def test_invalid_thinking_configuration_is_rejected(self):
        with patch.dict(os.environ, {"LLM_THINKING": "invalid"}, clear=True):
            with self.assertRaises(ModelError):
                OpenAICompatibleModel("https://example.invalid", "test-secret", "fixture")

    def test_cli_env_limits_apply_and_cli_flag_overrides_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "local.env"
            path.write_text("MAX_MODEL_CALLS=1\nMAX_AGENT_STEPS=1\n", encoding="utf-8")
            command = [sys.executable, "-m", "src.cli", "--env-file", str(path), "--mode", "mock",
                       "--topic", "新品推广", "--budget", "10", "--deadline-hours", "8",
                       "--request-id", "env-test", "--store-dir", str(Path(folder) / "store"), "--max-steps", "2"]
            env = {k: v for k, v in os.environ.items() if k not in ("MAX_MODEL_CALLS", "MAX_AGENT_STEPS")}
            response = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, encoding="utf-8")
            result = json.loads(response.stdout)
            self.assertEqual(response.returncode, 2)
            self.assertEqual(result["metadata"]["max_steps"], 2)
            self.assertEqual(result["model_calls"], 1)


if __name__ == "__main__":
    unittest.main()
