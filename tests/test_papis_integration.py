import tempfile
import unittest
from pathlib import Path

from papersearch import db
from papersearch.papis_integration import ensure_papis_library, sync_paper_to_papis


PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"


def config_for(path: Path, use_git_lfs: bool = False):
    return {
        "papis": {
            "enabled": True,
            "library_path": str(path),
            "auto_commit": False,
            "auto_push": False,
            "use_git_lfs": use_git_lfs,
            "write_cli_config": False,
            "folder_template": "{primary_task}/{primary_target}/{year}-{slug}",
        },
        "storage": {"database_path_resolved": str(path / "papers.sqlite")},
    }


class PapisIntegrationTests(unittest.TestCase):
    def test_sync_saved_paper_without_pdf_creates_pending_entry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = config_for(root / "library")
            db_path = root / "papers.sqlite"
            db.init_db(db_path)
            paper = {
                "source": "arXiv",
                "source_id": "2605.00001v1",
                "title": "Agentic Static Analysis for Vulnerability Detection",
                "abstract": "A software security paper.",
                "task_labels": ["LLM/Agent应用", "静态分析"],
                "target_labels": ["C/C++ OSS"],
                "relevance_score": 10,
                "year": 2026,
            }
            with db.connect(db_path) as conn:
                self.assertTrue(db.upsert_paper(conn, paper))
                row = db.list_papers(conn)[0]
                db.set_paper_flags(conn, row["id"], {"is_saved": True})

                result = sync_paper_to_papis(conn, row["id"], config)
                updated = db.get_paper(conn, row["id"])

            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "pending_pdf")
            self.assertEqual(updated["papis_status"], "pending_pdf")
            info = Path(result["folder"]) / "info.yaml"
            self.assertTrue(info.exists())
            self.assertIn("pdf-missing", info.read_text(encoding="utf-8"))

    def test_attach_pdf_updates_files_and_synced_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = config_for(root / "library")
            pdf_path = root / "manual.pdf"
            pdf_path.write_bytes(PDF_BYTES)
            db_path = root / "papers.sqlite"
            db.init_db(db_path)
            paper = {
                "source": "arXiv",
                "title": "Patch Generation with Agents",
                "abstract": "A software security paper.",
                "task_labels": ["LLM/Agent应用"],
                "target_labels": ["General OSS"],
                "relevance_score": 10,
                "year": 2026,
            }
            with db.connect(db_path) as conn:
                self.assertTrue(db.upsert_paper(conn, paper))
                row = db.list_papers(conn)[0]
                db.set_paper_flags(conn, row["id"], {"is_saved": True})

                result = sync_paper_to_papis(conn, row["id"], config, pdf_path=str(pdf_path))
                updated = db.get_paper(conn, row["id"])

            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "synced")
            self.assertEqual(updated["papis_status"], "synced")
            self.assertTrue(Path(updated["local_pdf_path"]).exists())
            info = Path(result["folder"]) / "info.yaml"
            text = info.read_text(encoding="utf-8")
            self.assertIn("files:", text)
            self.assertIn(".pdf", text)

    def test_git_lfs_rule_is_written_when_enabled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            library = Path(temp_dir) / "library"
            ensure_papis_library(config_for(library, use_git_lfs=True))
            self.assertIn("*.pdf filter=lfs", (library / ".gitattributes").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
