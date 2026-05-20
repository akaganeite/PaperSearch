import tempfile
import unittest
from pathlib import Path

from papersearch import db


class DeletedPaperTests(unittest.TestCase):
    def test_delete_creates_tombstone_and_blocks_reinsert(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "papersearch.sqlite"
            db.init_db(db_path)
            paper = {
                "source": "arXiv",
                "source_id": "1234.5678",
                "title": "LLM-Assisted Static Analysis for Vulnerability Detection",
                "abstract": "A software security paper.",
                "authors": ["A. Author"],
                "url": "https://example.test/paper",
                "venue": "",
                "year": 2026,
                "published_at": "2026-05-01T00:00:00Z",
                "source_categories": ["cs.CR"],
                "task_labels": ["LLM/Agent应用", "静态分析"],
                "target_labels": ["综述"],
                "relevance_score": 42,
                "recommendation_reason": "test",
            }

            with db.connect(db_path) as conn:
                self.assertTrue(db.upsert_paper(conn, paper))
                active = db.list_papers(conn)
                self.assertEqual(len(active), 1)

                deleted = db.delete_paper(conn, active[0]["id"])
                self.assertIsNotNone(deleted)
                self.assertEqual(db.list_papers(conn), [])
                self.assertEqual(len(db.list_papers(conn, status="deleted")), 1)

                self.assertFalse(db.upsert_paper(conn, paper))
                self.assertEqual(db.list_papers(conn), [])
                self.assertEqual(len(db.list_papers(conn, status="deleted")), 1)

    def test_sort_by_added_time(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "papersearch.sqlite"
            db.init_db(db_path)
            first = {
                "source": "arXiv",
                "title": "First Paper",
                "abstract": "A software security paper.",
                "task_labels": ["LLM/Agent应用"],
                "target_labels": ["Unknown/Unclear"],
                "relevance_score": 10,
            }
            second = {
                "source": "arXiv",
                "title": "Second Paper",
                "abstract": "A software security paper.",
                "task_labels": ["LLM/Agent应用"],
                "target_labels": ["Unknown/Unclear"],
                "relevance_score": 20,
            }
            with db.connect(db_path) as conn:
                self.assertTrue(db.upsert_paper(conn, first))
                self.assertTrue(db.upsert_paper(conn, second))
                conn.execute("UPDATE papers SET first_seen_at = ? WHERE title = ?", ("2026-05-01T00:00:00Z", "First Paper"))
                conn.execute("UPDATE papers SET first_seen_at = ? WHERE title = ?", ("2026-05-02T00:00:00Z", "Second Paper"))

                newest = db.list_papers(conn, sort="added_desc")
                oldest = db.list_papers(conn, sort="added_asc")
                self.assertEqual([paper["title"] for paper in newest], ["Second Paper", "First Paper"])
                self.assertEqual([paper["title"] for paper in oldest], ["First Paper", "Second Paper"])

    def test_saved_summary_queue_skips_already_summarized(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "papersearch.sqlite"
            db.init_db(db_path)
            first = {
                "source": "arXiv",
                "title": "Saved Paper Without Summary",
                "abstract": "A software security paper.",
                "task_labels": ["LLM/Agent应用"],
                "target_labels": ["Unknown/Unclear"],
                "relevance_score": 10,
            }
            second = {
                "source": "arXiv",
                "title": "Saved Paper With Summary",
                "abstract": "Another software security paper.",
                "task_labels": ["LLM/Agent应用"],
                "target_labels": ["Unknown/Unclear"],
                "relevance_score": 10,
            }
            with db.connect(db_path) as conn:
                self.assertTrue(db.upsert_paper(conn, first))
                self.assertTrue(db.upsert_paper(conn, second))
                rows = db.list_papers(conn, sort="added_asc")
                for paper in rows:
                    db.set_paper_flags(conn, paper["id"], {"is_saved": True})
                summarized = next(paper for paper in rows if paper["title"] == "Saved Paper With Summary")
                db.update_llm_summary(conn, summarized["id"], {"summary_zh": "已有摘要"}, "deepseek-chat")

                queued = db.list_saved_papers_for_summary(conn)
                self.assertEqual([paper["title"] for paper in queued], ["Saved Paper Without Summary"])


if __name__ == "__main__":
    unittest.main()
