import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from papersearch import db
from papersearch.config import load_config
from papersearch.llm_prefilter import (
    _system_prompt,
    _user_prompt,
    backfill_existing,
    build_review_record,
    enqueue_candidates,
    normalize_decision,
    process_review_queue,
)
from papersearch.pipeline import run_update


class LlmPrefilterTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = str(Path(self.temp_dir.name) / "papers.sqlite")
        self.config = copy.deepcopy(load_config("configs/app.example.json"))
        self.config["storage"]["database_path_resolved"] = self.db_path
        self.config["llm_prefilter"].update(
            {
                "enabled": True,
                "max_per_update": 100,
                "max_workers": 2,
                "max_retries": 0,
                "retry_delay_seconds": 0,
            }
        )
        self.interest = self.config["interests"][0]
        db.init_db(self.db_path)

    def paper(self, title="LLM-Assisted Static Analysis for Vulnerability Detection"):
        return {
            "source": "arXiv",
            "source_id": "2608.00001",
            "title": title,
            "abstract": "We apply a large language model to static analysis for finding vulnerabilities in C/C++ open-source projects.",
            "venue": "",
            "year": 2026,
            "published_at": "2026-08-01T00:00:00Z",
            "source_categories": ["cs.CR"],
        }

    @staticmethod
    def accepted_response():
        return {
            "accept": True,
            "score": 88,
            "route": "llm_application",
            "task_labels": ["LLM/Agent应用", "静态分析", "not-allowed"],
            "target_labels": ["C/C++ OSS", "not-allowed"],
            "reason": "LLM 被用于开源 C/C++ 漏洞静态分析。",
            "confidence": 0.93,
        }

    def test_hard_exclude_never_calls_llm(self):
        paper = self.paper("LLM-guided Fuzzing for Smart Contract Vulnerabilities")
        with patch("papersearch.llm_prefilter.chat_json") as call:
            enqueued = enqueue_candidates(self.db_path, [paper], self.interest, self.config)
            reviewed = process_review_queue(self.db_path, self.interest, self.config, 10)
        call.assert_not_called()
        self.assertEqual(enqueued["candidates"], 0)
        self.assertEqual(reviewed["processed"], 0)

    def test_accepts_valid_response_and_reuses_cache(self):
        with patch("papersearch.llm_prefilter.chat_json", return_value=(self.accepted_response(), "deepseek-test")) as call:
            first_enqueue = enqueue_candidates(self.db_path, [self.paper()], self.interest, self.config)
            first_review = process_review_queue(self.db_path, self.interest, self.config, 10)
            second_enqueue = enqueue_candidates(self.db_path, [self.paper()], self.interest, self.config)
            second_review = process_review_queue(self.db_path, self.interest, self.config, 10)

        self.assertEqual(call.call_count, 1)
        self.assertEqual(first_enqueue["candidates"], 1)
        self.assertEqual(first_review["accepted"], 1)
        self.assertEqual(first_review["new"], 1)
        self.assertEqual(second_enqueue["cached"], 1)
        self.assertEqual(second_review["processed"], 0)
        with db.connect(self.db_path) as conn:
            papers = db.list_all_papers(conn)
            row = conn.execute("SELECT candidate_json FROM llm_prefilter_reviews").fetchone()
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0]["relevance_score"], 88)
        self.assertEqual(papers[0]["task_labels"], ["LLM/Agent应用", "静态分析"])
        self.assertEqual(papers[0]["target_labels"], ["C/C++ OSS"])
        self.assertEqual(row["candidate_json"], "{}")

    def test_reject_does_not_insert_paper(self):
        response = {
            "accept": False,
            "score": 22,
            "route": "unrelated",
            "task_labels": [],
            "target_labels": [],
            "reason": "仅研究语言模型本身。",
            "confidence": 0.9,
        }
        enqueue_candidates(self.db_path, [self.paper()], self.interest, self.config)
        with patch("papersearch.llm_prefilter.chat_json", return_value=(response, "deepseek-test")):
            result = process_review_queue(self.db_path, self.interest, self.config, 10)
        self.assertEqual(result["rejected"], 1)
        with db.connect(self.db_path) as conn:
            self.assertEqual(db.list_all_papers(conn), [])
            status = db.get_llm_prefilter_status(conn)
            candidate_json = conn.execute("SELECT candidate_json FROM llm_prefilter_reviews").fetchone()[0]
        self.assertEqual(status["rejected"], 1)
        self.assertEqual(candidate_json, "{}")

    def test_failure_stays_hidden_and_can_retry(self):
        enqueue_candidates(self.db_path, [self.paper()], self.interest, self.config)
        with patch("papersearch.llm_prefilter.chat_json", side_effect=RuntimeError("temporary outage")):
            failed = process_review_queue(self.db_path, self.interest, self.config, 10)
        self.assertEqual(failed["errors"], 1)
        with db.connect(self.db_path) as conn:
            self.assertEqual(db.list_all_papers(conn), [])
            self.assertEqual(db.get_llm_prefilter_status(conn)["pending"], 1)
            conn.execute("UPDATE llm_prefilter_reviews SET next_retry_at = ''")

        with patch("papersearch.llm_prefilter.chat_json", return_value=(self.accepted_response(), "deepseek-test")):
            retried = process_review_queue(self.db_path, self.interest, self.config, 10)
        self.assertEqual(retried["accepted"], 1)
        with db.connect(self.db_path) as conn:
            self.assertEqual(len(db.list_all_papers(conn)), 1)

    def test_llm_request_runs_without_holding_a_database_transaction(self):
        enqueue_candidates(self.db_path, [self.paper()], self.interest, self.config)

        def response_with_concurrent_write(*_args, **_kwargs):
            with db.connect(self.db_path) as conn:
                db.set_state(conn, "concurrent-ui-write", "ok")
            return self.accepted_response(), "deepseek-test"

        with patch("papersearch.llm_prefilter.chat_json", side_effect=response_with_concurrent_write):
            result = process_review_queue(self.db_path, self.interest, self.config, 10)

        self.assertEqual(result["accepted"], 1)
        with db.connect(self.db_path) as conn:
            self.assertEqual(db.get_state(conn, "concurrent-ui-write"), "ok")

    def test_content_profile_and_model_change_cache_key(self):
        first = build_review_record(self.paper(), self.interest, self.config, {"priority": 1})
        changed_paper = self.paper()
        changed_paper["abstract"] += " Additional evidence."
        second = build_review_record(changed_paper, self.interest, self.config, {"priority": 1})
        changed_profile = copy.deepcopy(self.interest)
        changed_profile["description"] += " Narrower."
        third = build_review_record(self.paper(), changed_profile, self.config, {"priority": 1})
        changed_model = copy.deepcopy(self.config)
        changed_model["llm_prefilter"]["model"] = "another-model"
        fourth = build_review_record(self.paper(), self.interest, changed_model, {"priority": 1})
        changed_prompt = copy.deepcopy(self.config)
        changed_prompt["llm_prefilter"]["prompt_version"] = "next"
        fifth = build_review_record(self.paper(), self.interest, changed_prompt, {"priority": 1})
        self.assertEqual(
            len({first["cache_key"], second["cache_key"], third["cache_key"], fourth["cache_key"], fifth["cache_key"]}),
            5,
        )

    def test_prompt_requires_security_context_for_generic_software_tasks(self):
        system_prompt = _system_prompt()
        user_prompt = _user_prompt(self.paper("Generic SysML Fault Localization"), self.interest, self.config)
        self.assertIn("generic bug fixing", system_prompt)
        self.assertIn("explicit evidence", user_prompt)
        self.assertIn("SysML/model errors", user_prompt)

    def test_saved_backfill_preserves_summary_but_unsaved_is_deleted(self):
        saved = self.paper("LLM Static Analysis Saved Paper")
        unsaved = self.paper("LLM Static Analysis Unsaved Paper")
        unsaved["source_id"] = "2608.00002"
        with db.connect(self.db_path) as conn:
            db.upsert_paper(conn, saved | {"task_labels": ["静态分析"], "target_labels": ["C/C++ OSS"], "relevance_score": 90})
            db.upsert_paper(conn, unsaved | {"task_labels": ["静态分析"], "target_labels": ["C/C++ OSS"], "relevance_score": 90})
            papers = db.list_all_papers(conn)
            saved_row = next(item for item in papers if item["title"] == saved["title"])
            db.set_paper_flags(conn, int(saved_row["id"]), {"is_saved": True})
            db.update_llm_summary(conn, int(saved_row["id"]), {"summary_zh": "已有摘要"}, "deepseek-test")

        rejection = {
            "accept": False,
            "score": 20,
            "route": "unrelated",
            "task_labels": [],
            "target_labels": [],
            "reason": "相关性不足。",
            "confidence": 0.8,
        }
        with patch("papersearch.llm_prefilter.load_config", return_value=self.config), patch(
            "papersearch.llm_prefilter.chat_json", return_value=(rejection, "deepseek-test")
        ):
            result = backfill_existing(limit=10)

        self.assertEqual(result["reviewed"], 2)
        with db.connect(self.db_path) as conn:
            papers = db.list_all_papers(conn)
        self.assertEqual([paper["title"] for paper in papers], [saved["title"]])
        self.assertEqual(papers[0]["relevance_score"], 20)
        self.assertIn("Saved 人工保留", papers[0]["recommendation_reason"])
        self.assertEqual(papers[0]["llm_summary"]["summary_zh"], "已有摘要")

    def test_accepted_backfill_is_idempotent_and_preserves_raw_metadata(self):
        paper = self.paper() | {"raw": {"context": "original source context"}}
        with db.connect(self.db_path) as conn:
            db.upsert_paper(conn, paper | {"relevance_score": 55})

        with patch("papersearch.llm_prefilter.load_config", return_value=self.config), patch(
            "papersearch.llm_prefilter.chat_json", return_value=(self.accepted_response(), "deepseek-test")
        ) as call:
            first = backfill_existing(limit=10)
            second = backfill_existing(limit=10)

        self.assertEqual(call.call_count, 1)
        self.assertEqual(first["reviewed"], 1)
        self.assertEqual(second["selected"], 0)
        with db.connect(self.db_path) as conn:
            papers = db.list_all_papers(conn)
            review_count = conn.execute("SELECT COUNT(*) FROM llm_prefilter_reviews").fetchone()[0]
        self.assertEqual(papers[0]["raw"], {"context": "original source context"})
        self.assertEqual(review_count, 1)

    def test_normalize_rejects_inconsistent_accept(self):
        decision = normalize_decision(
            self.accepted_response() | {"score": 45, "route": "llm_application"},
            self.interest,
            self.config,
        )
        self.assertFalse(decision["accept"])
        decision = normalize_decision(
            self.accepted_response() | {"route": "unexpected", "task_labels": ["not-allowed"]},
            self.interest,
            self.config,
        )
        self.assertFalse(decision["accept"])
        self.assertEqual(decision["task_labels"], [self.interest["fallback_task_label"]])

    def test_update_pipeline_only_inserts_llm_accepted_paper(self):
        self.config["pdf_resolver"]["enabled"] = False
        self.config["llm_summary"]["enabled"] = False
        with patch("papersearch.pipeline.load_config", return_value=self.config), patch(
            "papersearch.pipeline.arxiv.fetch", return_value=[self.paper()]
        ), patch("papersearch.llm_prefilter.chat_json", return_value=(self.accepted_response(), "deepseek-test")):
            result = run_update(source_filter="arxiv")

        self.assertEqual(result["sources"]["arXiv"]["fetched"], 1)
        self.assertEqual(result["sources"]["arXiv"]["kept"], 1)
        self.assertEqual(result["sources"]["arXiv"]["new"], 1)
        self.assertEqual(result["llm_prefilter"]["accepted"], 1)
        with db.connect(self.db_path) as conn:
            self.assertEqual(len(db.list_all_papers(conn)), 1)


if __name__ == "__main__":
    unittest.main()
