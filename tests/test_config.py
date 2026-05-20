import json
import tempfile
import unittest
from pathlib import Path

from papersearch.config import config_check, load_config, redact_config


class ConfigTests(unittest.TestCase):
    def test_partial_config_merges_profile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profile = root / "profile.json"
            database = root / "papers.sqlite"
            profile.write_text(
                json.dumps(
                    {
                        "summary_profile": {"focus": "program analysis"},
                        "interests": [
                            {
                                "id": "demo",
                                "llm_agent_terms": ["llm"],
                                "security_software_terms": ["static analysis"],
                                "task_label_rules": [{"label": "LLM", "match": "llm_agent"}],
                                "target_label_rules": [{"label": "Code", "terms": ["code"]}],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            config_path = root / "local.json"
            config_path.write_text(
                json.dumps(
                    {
                        "profile_path": str(profile),
                        "storage": {"database_path": str(database)},
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(str(config_path))

        self.assertEqual(config["interests"][0]["id"], "demo")
        self.assertEqual(config["summary_profile"]["focus"], "program analysis")
        self.assertEqual(config["storage"]["database_path_resolved"], str(database))

    def test_redacts_direct_api_key_env(self):
        redacted = redact_config({"llm_summary": {"api_key_env": "sk" + "-test-secret-value-1234567890"}})
        self.assertEqual(redacted["llm_summary"]["api_key_env"], "[redacted]")

    def test_config_check_accepts_example_config(self):
        result = config_check("configs/app.example.json")
        self.assertTrue(result["ok"], result)
        self.assertIn("software_security_llm_agent_applications", result["interests"])


if __name__ == "__main__":
    unittest.main()
