from __future__ import annotations

import argparse
import json

from . import db
from .config import config_check, load_config, redact_config
from .llm_summary import summarize_saved_papers
from .pdf_resolver import resolve_missing_pdfs
from .pipeline import reclassify_existing, run_update
from .server import run_server


def main() -> None:
    parser = argparse.ArgumentParser(prog="papersearch")
    parser.add_argument("--config", default=None, help="Path to interests config JSON.")
    subparsers = parser.add_subparsers(dest="command")

    update_parser = subparsers.add_parser("update", help="Fetch and classify papers.")
    update_parser.add_argument("--bootstrap", action="store_true", help="Use bootstrap window.")
    update_parser.add_argument(
        "--source",
        default="",
        choices=["", "arxiv", "cspapers", "venue-preprints", "venues"],
        help="Limit to one source.",
    )

    pdf_parser = subparsers.add_parser("resolve-pdfs", help="Find open-access PDF URLs for existing papers.")
    pdf_parser.add_argument("--limit", type=int, default=None)
    pdf_parser.add_argument("--force", action="store_true", help="Re-check papers that already have a PDF URL.")
    pdf_parser.add_argument("--unpaywall-only", action="store_true", help="Only check Unpaywall using existing DOI metadata.")
    pdf_parser.add_argument("--publisher-only", action="store_true", help="Only resolve ACM/IEEE landing pages using existing DOI metadata.")

    summary_parser = subparsers.add_parser("summarize-saved", help="Generate DeepSeek summaries for saved papers without existing summaries.")
    summary_parser.add_argument("--limit", type=int, default=None)

    serve_parser = subparsers.add_parser("serve", help="Run the local web app.")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)

    subparsers.add_parser("init-db", help="Create the SQLite database schema.")
    subparsers.add_parser("reclassify", help="Re-apply current interest rules to existing papers.")
    subparsers.add_parser("show-config", help="Print the resolved config.")
    subparsers.add_parser("config-check", help="Validate the resolved config without exposing secrets.")

    args = parser.parse_args()
    if args.command == "update":
        summary = run_update(args.config, bootstrap=args.bootstrap, source_filter=args.source)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return
    if args.command == "serve":
        run_server(args.host, args.port, args.config)
        return
    if args.command == "resolve-pdfs":
        print(
            json.dumps(
                resolve_missing_pdfs(
                    args.config,
                    limit=args.limit,
                    force=args.force,
                    unpaywall_only=args.unpaywall_only,
                    publisher_only=args.publisher_only,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.command == "summarize-saved":
        print(json.dumps(summarize_saved_papers(args.config, limit=args.limit), ensure_ascii=False, indent=2))
        return
    if args.command == "init-db":
        config = load_config(args.config)
        db.init_db(config["storage"]["database_path_resolved"])
        print(config["storage"]["database_path_resolved"])
        return
    if args.command == "reclassify":
        print(json.dumps(reclassify_existing(args.config), ensure_ascii=False, indent=2))
        return
    if args.command == "show-config":
        print(json.dumps(redact_config(load_config(args.config)), ensure_ascii=False, indent=2))
        return
    if args.command == "config-check":
        print(json.dumps(config_check(args.config), ensure_ascii=False, indent=2))
        return

    parser.print_help()
