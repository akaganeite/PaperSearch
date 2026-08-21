import unittest

from papersearch.classify import enrich_and_filter
from papersearch.config import load_config


class ClassifyTests(unittest.TestCase):
    def setUp(self):
        self.interest = load_config()["interests"][0]

    def test_keeps_llm_security_application(self):
        paper = {
            "title": "LLM-Assisted Static Analysis for Vulnerability Detection",
            "abstract": "We apply large language models to static analysis for finding vulnerabilities in C/C++ open-source projects.",
            "venue": "",
            "published_at": "2026-05-01T00:00:00Z",
        }
        keep, enriched, reason = enrich_and_filter(paper, self.interest)
        self.assertTrue(keep, reason)
        self.assertIn("LLM/Agent应用", enriched["task_labels"])
        self.assertIn("静态分析", enriched["task_labels"])
        self.assertIn("C/C++ OSS", enriched["target_labels"])

    def test_excludes_fuzzing(self):
        paper = {
            "title": "LLM-guided Fuzzing for Vulnerability Discovery",
            "abstract": "A fuzzer improves coverage-guided fuzzing.",
            "venue": "ICSE",
            "published_at": "2026-05-01T00:00:00Z",
        }
        keep, _enriched, reason = enrich_and_filter(paper, self.interest)
        self.assertFalse(keep)
        self.assertIn("硬过滤", reason)

    def test_excludes_smart_contracts(self):
        paper = {
            "title": "LLM-Assisted Static Analysis for Smart Contract Vulnerabilities",
            "abstract": "We detect reentrancy bugs in Solidity smart contracts.",
            "venue": "ICSE",
            "published_at": "2026-05-01T00:00:00Z",
        }
        keep, _enriched, reason = enrich_and_filter(paper, self.interest)
        self.assertFalse(keep)
        self.assertIn("硬过滤", reason)

    def test_keeps_top_venue_traditional_method(self):
        paper = {
            "title": "Static Analysis for Patch Correctness in Linux Kernel Drivers",
            "abstract": "This paper studies patch generation, patch correctness, patch validation, static analysis, and root cause analysis for Linux kernel driver vulnerabilities.",
            "venue": "USENIX Security",
            "published_at": "2025-08-01T00:00:00Z",
        }
        keep, enriched, reason = enrich_and_filter(paper, self.interest)
        self.assertTrue(keep, reason)
        self.assertIn("trditional", enriched["task_labels"])
        self.assertIn("Linux Kernel", enriched["target_labels"])

    def test_rejects_top_venue_traditional_without_strict_keyword(self):
        paper = {
            "title": "Understanding Binary Code Similarity for Real-World Vulnerability Detection",
            "abstract": "A study of vulnerability detection in binary programs.",
            "venue": "FSE",
            "published_at": "2026-05-01T00:00:00Z",
        }
        keep, _enriched, reason = enrich_and_filter(paper, self.interest)
        self.assertFalse(keep)
        self.assertIn("严格关键词", reason)

    def test_rejects_pure_llm_research(self):
        paper = {
            "title": "Improving Large Language Model Pretraining with New Tokenizers",
            "abstract": "We study model architecture and language model training for NLP benchmark performance.",
            "venue": "",
            "published_at": "2026-05-01T00:00:00Z",
        }
        keep, _enriched, _reason = enrich_and_filter(paper, self.interest)
        self.assertFalse(keep)

    def test_adds_survey_target_label(self):
        paper = {
            "title": "A Survey of LLM-Assisted Vulnerability Detection",
            "abstract": "This survey reviews large language model applications in software security, vulnerability detection, static analysis, patch validation, and automated repair.",
            "venue": "",
            "published_at": "2026-05-01T00:00:00Z",
        }
        keep, enriched, reason = enrich_and_filter(paper, self.interest)
        self.assertTrue(keep, reason)
        self.assertIn("综述", enriched["target_labels"])

    def test_prioritizes_agent_tool_security_evaluations(self):
        paper = {
            "title": "Evaluating Claude Code and Codex for Vulnerability Repair",
            "abstract": "We evaluate coding agents on static analysis, vulnerability localization, and patch generation for C/C++ projects.",
            "venue": "",
            "published_at": "2026-05-01T00:00:00Z",
        }
        keep, enriched, reason = enrich_and_filter(paper, self.interest)
        self.assertTrue(keep, reason)
        self.assertIn("Agent工具评估", enriched["task_labels"])
        self.assertIn("Agent 工具评估命中", enriched["recommendation_reason"])
        self.assertGreaterEqual(enriched["relevance_score"], 80)

    def test_rejects_low_score_matches(self):
        paper = {
            "title": "LLM Vulnerability",
            "abstract": "A large language model vulnerability note.",
            "venue": "",
            "published_at": "",
        }
        keep, _enriched, reason = enrich_and_filter(paper, self.interest)
        self.assertFalse(keep)
        self.assertIn("推荐分低于入库阈值", reason)


if __name__ == "__main__":
    unittest.main()
