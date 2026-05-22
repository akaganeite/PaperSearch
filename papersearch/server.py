from __future__ import annotations

import argparse
import cgi
import json
import mimetypes
import tempfile
import threading
import time
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import db
from .config import ROOT_DIR, load_config, redact_config
from .llm_summary import summarize_saved_papers
from .papis_integration import attach_pdf_to_paper, papis_status, sync_paper_to_papis
from .pipeline import run_update


STATIC_DIR = ROOT_DIR / "papersearch" / "static"


class AppState:
    def __init__(self, config_path: str | None):
        self.config_path = config_path
        self.config = load_config(config_path)
        self.db_path = self.config["storage"]["database_path_resolved"]
        db.init_db(self.db_path)
        self.update_lock = threading.Lock()
        self.summary_lock = threading.Lock()
        self.papis_lock = threading.Lock()
        self.update_status: dict[str, Any] = {"running": False, "last_summary": None}
        self.summary_status: dict[str, Any] = {"running": False, "last_summary": None}
        self.scheduler_started = False


STATE: AppState | None = None


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")


def _public_config(config: dict[str, Any]) -> dict[str, Any]:
    public_config = redact_config(config)
    public_config["storage"] = {"database_path": public_config.get("storage", {}).get("database_path")}
    return public_config


class PaperSearchHandler(BaseHTTPRequestHandler):
    server_version = "PaperSearch/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {self.address_string()} {fmt % args}")

    @property
    def state(self) -> AppState:
        assert STATE is not None
        return STATE

    def _send_json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = _json_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, value: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = value.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self._send_text("Not found", HTTPStatus.NOT_FOUND)
            return
        body = path.read_bytes()
        mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API.
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/":
            self._send_file(STATIC_DIR / "index.html")
            return
        if path.startswith("/static/"):
            static_path = STATIC_DIR / path.removeprefix("/static/")
            if STATIC_DIR not in static_path.resolve().parents and static_path.resolve() != STATIC_DIR:
                self._send_text("Forbidden", HTTPStatus.FORBIDDEN)
                return
            self._send_file(static_path)
            return
        if path == "/api/config":
            self._send_json(_public_config(self.state.config))
            return
        if path == "/api/update-status":
            self._send_json(self.state.update_status)
            return
        if path == "/api/summary-status":
            self._send_json(self.state.summary_status)
            return
        if path == "/api/papis/status":
            self._send_json(papis_status(self.state.config_path))
            return

        with db.connect(self.state.db_path) as conn:
            if path == "/api/papers":
                papers = db.list_papers(
                    conn,
                    query=query.get("q", [""])[0],
                    source=query.get("source", [""])[0],
                    task=query.get("task", [""])[0],
                    target=query.get("target", [""])[0],
                    status=query.get("status", [""])[0],
                    sort=query.get("sort", ["relevance"])[0],
                    limit=int(query.get("limit", ["200"])[0]),
                )
                self._send_json({"papers": papers})
                return
            if path == "/api/stats":
                self._send_json(db.get_stats(conn))
                return
            if path == "/api/run-logs":
                self._send_json({"logs": db.list_run_logs(conn)})
                return

        self._send_text("Not found", HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API.
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/papers/") and path.endswith("/pdf"):
            self._handle_pdf_upload(path)
            return

        length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._send_json({"error": "Invalid JSON"}, HTTPStatus.BAD_REQUEST)
            return

        if path == "/api/update":
            if self.state.update_lock.locked():
                self._send_json({"ok": False, "running": True, "message": "Update already running"}, HTTPStatus.CONFLICT)
                return
            bootstrap = bool(payload.get("bootstrap", False))
            source = str(payload.get("source", "") or "")

            def worker() -> None:
                with self.state.update_lock:
                    self.state.update_status = {"running": True, "last_summary": self.state.update_status.get("last_summary")}
                    summary = run_update(self.state.config_path, bootstrap=bootstrap, source_filter=source)
                    self.state.update_status = {"running": False, "last_summary": summary}
                    if summary.get("llm_summaries") is not None:
                        self.state.summary_status = {"running": False, "last_summary": summary["llm_summaries"]}

            threading.Thread(target=worker, daemon=True).start()
            self._send_json({"ok": True, "running": True})
            return

        if path == "/api/summaries/run":
            if self.state.summary_lock.locked():
                self._send_json({"ok": False, "running": True, "message": "Summary update already running"}, HTTPStatus.CONFLICT)
                return
            if self.state.update_lock.locked():
                self._send_json({"ok": False, "running": True, "message": "Main update is running"}, HTTPStatus.CONFLICT)
                return
            limit = payload.get("limit")

            def worker() -> None:
                with self.state.summary_lock:
                    self.state.summary_status = {"running": True, "last_summary": self.state.summary_status.get("last_summary")}
                    summary = summarize_saved_papers(self.state.config_path, limit=int(limit) if limit else None)
                    self.state.summary_status = {"running": False, "last_summary": summary}

            threading.Thread(target=worker, daemon=True).start()
            self._send_json({"ok": True, "running": True})
            return

        if path.startswith("/api/papers/") and path.endswith("/flags"):
            try:
                paper_id = int(path.split("/")[3])
            except (IndexError, ValueError):
                self._send_json({"error": "Invalid paper id"}, HTTPStatus.BAD_REQUEST)
                return
            with db.connect(self.state.db_path) as conn:
                paper = db.set_paper_flags(conn, paper_id, payload)
                if paper is None:
                    self._send_json({"error": "Paper not found"}, HTTPStatus.NOT_FOUND)
                else:
                    papis_result = None
                    if (
                        payload.get("is_saved") is True
                        and self.state.config.get("papis", {}).get("enabled", False)
                        and self.state.config.get("papis", {}).get("auto_archive_on_save", True)
                        and not paper.get("papis_id")
                    ):
                        with self.state.papis_lock:
                            papis_result = sync_paper_to_papis(conn, paper_id, self.state.config)
                            paper = db.get_paper(conn, paper_id) or paper
                    self._send_json({"paper": paper, "papis": papis_result})
            return

        if path.startswith("/api/papers/") and path.endswith("/papis/sync"):
            try:
                paper_id = int(path.split("/")[3])
            except (IndexError, ValueError):
                self._send_json({"error": "Invalid paper id"}, HTTPStatus.BAD_REQUEST)
                return
            with self.state.papis_lock:
                with db.connect(self.state.db_path) as conn:
                    result = sync_paper_to_papis(conn, paper_id, self.state.config)
                    paper = db.get_paper(conn, paper_id)
            self._send_json({"paper": paper, "papis": result})
            return

        if path.startswith("/api/papers/") and path.endswith("/delete"):
            try:
                paper_id = int(path.split("/")[3])
            except (IndexError, ValueError):
                self._send_json({"error": "Invalid paper id"}, HTTPStatus.BAD_REQUEST)
                return
            with db.connect(self.state.db_path) as conn:
                deleted = db.delete_paper(conn, paper_id)
                if deleted is None:
                    self._send_json({"error": "Paper not found"}, HTTPStatus.NOT_FOUND)
                else:
                    self._send_json({"paper": deleted})
            return

        self._send_text("Not found", HTTPStatus.NOT_FOUND)

    def _handle_pdf_upload(self, path: str) -> None:
        try:
            paper_id = int(path.split("/")[3])
        except (IndexError, ValueError):
            self._send_json({"error": "Invalid paper id"}, HTTPStatus.BAD_REQUEST)
            return
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self._send_json({"error": "Expected multipart/form-data"}, HTTPStatus.BAD_REQUEST)
            return
        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": content_type,
                "CONTENT_LENGTH": self.headers.get("Content-Length", "0"),
            },
        )
        field = form["pdf"] if "pdf" in form else None
        if field is None or not getattr(field, "file", None):
            self._send_json({"error": "Missing pdf file field"}, HTTPStatus.BAD_REQUEST)
            return
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as handle:
            temp_path = Path(handle.name)
            shutil_buffer = field.file.read()
            handle.write(shutil_buffer)
        try:
            with self.state.papis_lock:
                result = attach_pdf_to_paper(self.state.config_path, paper_id, str(temp_path))
                with db.connect(self.state.db_path) as conn:
                    paper = db.get_paper(conn, paper_id)
            self._send_json({"paper": paper, "papis": result})
        finally:
            try:
                temp_path.unlink()
            except OSError:
                pass


def _scheduler_loop(state: AppState) -> None:
    configured_time = state.config.get("scheduler", {}).get("daily_update_local_time", "08:30")
    now_at_start = datetime.now()
    last_run_date = now_at_start.strftime("%Y-%m-%d") if now_at_start.strftime("%H:%M") >= configured_time else ""
    while True:
        try:
            now = datetime.now()
            today = now.strftime("%Y-%m-%d")
            if now.strftime("%H:%M") >= configured_time and last_run_date != today:
                if not state.update_lock.locked():
                    with state.update_lock:
                        state.update_status = {"running": True, "last_summary": state.update_status.get("last_summary")}
                        summary = run_update(state.config_path, bootstrap=False)
                        state.update_status = {"running": False, "last_summary": summary}
                        if summary.get("llm_summaries") is not None:
                            state.summary_status = {"running": False, "last_summary": summary["llm_summaries"]}
                    last_run_date = today
            time.sleep(60)
        except Exception as exc:  # noqa: BLE001 - scheduler must stay alive.
            state.update_status = {
                "running": False,
                "last_summary": state.update_status.get("last_summary"),
                "scheduler_error": str(exc),
            }
            time.sleep(60)


def run_server(host: str, port: int, config_path: str | None = None) -> None:
    global STATE
    STATE = AppState(config_path)
    if not STATE.scheduler_started:
        threading.Thread(target=_scheduler_loop, args=(STATE,), daemon=True).start()
        STATE.scheduler_started = True

    server = ThreadingHTTPServer((host, port), PaperSearchHandler)
    print(f"PaperSearch running at http://{host}:{port}")
    print(f"Database: {STATE.db_path}")
    server.serve_forever()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--config", default=None)
    args = parser.parse_args(argv)
    run_server(args.host, args.port, args.config)


if __name__ == "__main__":
    main()
