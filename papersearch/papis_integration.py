from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
from configparser import ConfigParser
from pathlib import Path
from typing import Any, Dict

from . import db
from .config import load_config, resolve_path
from .sources.common import SourceError, configure_network, current_proxy_url, http_get_bytes
from .text import normalize_title


PDF_EXTENSIONS = {".pdf"}


def _clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _cfg(config: Dict[str, Any]) -> Dict[str, Any]:
    return config.get("papis", {}) if isinstance(config.get("papis"), dict) else {}


def _enabled(config: Dict[str, Any]) -> bool:
    return bool(_cfg(config).get("enabled", False))


def _expand_path(path_value: str) -> Path:
    return Path(path_value).expanduser()


def papis_library_path(config: Dict[str, Any]) -> Path:
    value = str(_cfg(config).get("library_path", "~/Library/Application Support/PaperSearch/papis-library") or "")
    return _expand_path(value)


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 60) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
        )
        return completed.returncode, completed.stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def _shell_single_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _proxy_hostport(proxy_url: str) -> tuple[str, str] | None:
    value = proxy_url.strip()
    if not value:
        return None
    parsed = urllib.parse.urlparse(value if "://" in value else f"http://{value}")
    if not parsed.hostname or not parsed.port:
        return None
    proxy_type = "5" if parsed.scheme.lower().startswith("socks") else "connect"
    return f"{parsed.hostname}:{parsed.port}", proxy_type


def _git_ssh_command_for_proxy(proxy_url: str) -> str:
    parsed = _proxy_hostport(proxy_url)
    if not parsed:
        return ""
    hostport, proxy_type = parsed
    proxy_command = f"nc -X {proxy_type} -x {hostport} %h %p"
    return (
        "ssh "
        f"-o ProxyCommand={_shell_single_quote(proxy_command)} "
        "-o ConnectTimeout=20 "
        "-o ServerAliveInterval=5 "
        "-o ServerAliveCountMax=3"
    )


def _configure_git_transport(config: Dict[str, Any], library_path: Path) -> str:
    cfg = _cfg(config)
    mode = cfg.get("git_ssh_proxy", "auto")
    if mode is False or str(mode).lower() in {"0", "false", "off", "none", "direct"}:
        _run(["git", "config", "--unset", "core.sshCommand"], cwd=library_path)
        return ""
    proxy_url = str(cfg.get("git_ssh_proxy_url") or current_proxy_url() or "").strip()
    command = str(cfg.get("git_ssh_command") or "").strip() or _git_ssh_command_for_proxy(proxy_url)
    if command:
        _run(["git", "config", "core.sshCommand", command], cwd=library_path)
    return command


def _tool_exists(name: str) -> bool:
    return shutil.which(name) is not None


def _papis_config_dirs() -> list[Path]:
    override = os.environ.get("PAPIS_CONFIG_DIR")
    if override:
        return [Path(override).expanduser()]
    if sys.platform == "darwin":
        return [Path.home() / "Library" / "Application Support" / "papis"]
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return [(Path(xdg).expanduser() if xdg else Path.home() / ".config") / "papis"]


def _write_papis_config(config: Dict[str, Any], library_path: Path) -> None:
    if _cfg(config).get("write_cli_config", True) is False:
        return
    library_name = str(_cfg(config).get("library_name", "papersearch") or "papersearch")
    for config_dir in _papis_config_dirs():
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / "config"
        parser = ConfigParser(interpolation=None)
        if config_path.exists():
            parser.read(config_path, encoding="utf-8")
        if not parser.has_section("settings"):
            parser.add_section("settings")
        parser.set("settings", "default-library", library_name)
        if not parser.has_section(library_name):
            parser.add_section(library_name)
        parser.set(library_name, "dir", str(library_path))
        with config_path.open("w", encoding="utf-8") as handle:
            parser.write(handle)


def ensure_papis_library(config: Dict[str, Any], install_tools: bool = False) -> Dict[str, Any]:
    configure_network(config.get("network", {}))
    cfg = _cfg(config)
    library_path = papis_library_path(config)
    library_path.mkdir(parents=True, exist_ok=True)
    (library_path / "documents").mkdir(exist_ok=True)
    messages: list[str] = []

    if install_tools and not _tool_exists("papis") and _tool_exists("uv"):
        code, output = _run(["uv", "tool", "install", "papis"], timeout=300)
        messages.append(f"uv tool install papis: {code} {output[:300]}")
    if install_tools and cfg.get("use_git_lfs", True) and not _tool_exists("git-lfs") and _tool_exists("brew"):
        code, output = _run(["brew", "install", "git-lfs"], timeout=600)
        messages.append(f"brew install git-lfs: {code} {output[:300]}")

    if not (library_path / ".git").exists():
        _run(["git", "init"], cwd=library_path)
    _run(["git", "config", "user.name", "PaperSearch"], cwd=library_path)
    _run(["git", "config", "user.email", "papersearch@example.invalid"], cwd=library_path)

    remote_url = str(cfg.get("remote_url", "") or "").strip()
    if remote_url:
        code, remotes = _run(["git", "remote"], cwd=library_path)
        if code == 0 and "origin" not in remotes.split():
            _run(["git", "remote", "add", "origin", remote_url], cwd=library_path)
    git_ssh_command = _configure_git_transport(config, library_path)

    if cfg.get("use_git_lfs", True):
        if _tool_exists("git-lfs"):
            _run(["git", "lfs", "install"], cwd=library_path)
        gitattributes = library_path / ".gitattributes"
        lfs_rule = "*.pdf filter=lfs diff=lfs merge=lfs -text\n"
        if not gitattributes.exists() or lfs_rule.strip() not in gitattributes.read_text(encoding="utf-8", errors="ignore"):
            with gitattributes.open("a", encoding="utf-8") as handle:
                handle.write(lfs_rule)

    symlink = str(cfg.get("symlink_path", "") or "").strip()
    if symlink:
        symlink_path = _expand_path(symlink)
        if not symlink_path.exists():
            try:
                symlink_path.symlink_to(library_path, target_is_directory=True)
            except OSError as exc:
                messages.append(f"symlink skipped: {exc}")

    _write_papis_config(config, library_path)
    return {
        "library_path": str(library_path),
        "papis_available": _tool_exists("papis"),
        "git_lfs_available": _tool_exists("git-lfs"),
        "git_ssh_command": git_ssh_command,
        "messages": messages,
    }


def _sanitize_part(value: object, fallback: str) -> str:
    text = _clean(value).replace("/", "-").replace(":", "-")
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"[^\w.\-一-龥]+", "-", text, flags=re.U).strip(".-")
    return (text or fallback)[:90]


def _slug(title: str) -> str:
    normalized = normalize_title(title)
    return _sanitize_part(normalized.replace(" ", "-"), "paper")[:70]


def _notes_filename(title: str) -> str:
    text = _clean(title)
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " - ", text)
    text = re.sub(r"\s+", " ", text).strip(" .-") or "Untitled"
    max_title_bytes = 240
    while len(text.encode("utf-8")) > max_title_bytes:
        text = text[:-1].rstrip(" .-")
    return f"{text}.md"


def _folder_for_paper(config: Dict[str, Any], paper: Dict[str, Any]) -> Path:
    cfg = _cfg(config)
    template = str(cfg.get("folder_template", "{primary_task}/{primary_target}/{year}-{slug}") or "{year}-{slug}")
    tasks = paper.get("task_labels") or []
    targets = paper.get("target_labels") or []
    values = {
        "primary_task": _sanitize_part(tasks[0] if tasks else "uncategorized", "uncategorized"),
        "primary_target": _sanitize_part(targets[0] if targets else "unknown", "unknown"),
        "year": _sanitize_part(paper.get("year") or "unknown", "unknown"),
        "slug": _slug(str(paper.get("title", "") or "paper")),
        "paper_id": str(paper.get("id", "")),
    }
    rendered = template.format(**values)
    return Path(*[_sanitize_part(part, "paper") for part in Path(rendered).parts if part not in {"", "."}])


def _yaml_scalar(value: object, indent: int = 0) -> str:
    text = str(value or "")
    if "\n" in text:
        padding = " " * (indent + 2)
        return "|\n" + "\n".join(padding + line for line in text.splitlines())
    return json.dumps(text, ensure_ascii=False)


def _write_yaml(path: Path, metadata: Dict[str, Any]) -> None:
    lines: list[str] = []
    for key, value in metadata.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            if value:
                for item in value:
                    lines.append(f"  - {_yaml_scalar(item, 2)}")
            else:
                lines.append("  []")
        elif isinstance(value, dict):
            lines.append(f"{key}:")
            for sub_key, sub_value in value.items():
                lines.append(f"  {sub_key}: {_yaml_scalar(sub_value, 2)}")
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            lines.append(f"{key}: {value}")
        elif isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        else:
            lines.append(f"{key}: {_yaml_scalar(value)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _find_existing_folder(library_path: Path, paper: Dict[str, Any]) -> Path | None:
    wanted_id = str(paper.get("papis_id") or "")
    wanted_doi = str(paper.get("doi") or "").lower()
    wanted_source = str(paper.get("source_id") or "")
    wanted_title = normalize_title(str(paper.get("title", "")))
    for info in library_path.glob("**/info.yaml"):
        try:
            text = info.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if wanted_id and f"papis_id: {json.dumps(wanted_id)}" in text:
            return info.parent
        if wanted_doi and re.search(rf"^doi:\s*[\"']?{re.escape(wanted_doi)}[\"']?\s*$", text, flags=re.I | re.M):
            return info.parent
        if wanted_source and f"papersearch_source_id: {json.dumps(wanted_source)}" in text:
            return info.parent
        match = re.search(r"^title:\s*(.+)$", text, flags=re.M)
        if match and normalize_title(match.group(1).strip().strip("\"'")) == wanted_title:
            return info.parent
    return None


def _download_pdf(url: str, destination: Path, timeout: int, allow_insecure_tls: bool = False) -> tuple[bool, str]:
    if not url:
        return False, "No PDF URL."
    try:
        body, content_type = http_get_bytes(
        url,
            timeout=timeout,
            retries=1,
            headers={"Accept": "application/pdf,*/*;q=0.8"},
            allow_insecure_tls=allow_insecure_tls,
        )
    except SourceError as exc:
        return False, str(exc)
    content_type = content_type.lower()
    if not body.startswith(b"%PDF") and "application/pdf" not in content_type:
        return False, f"Downloaded content is not a PDF ({content_type or 'unknown content-type'})."
    destination.write_bytes(body)
    return True, ""


def _copy_uploaded_pdf(source: Path, destination: Path) -> tuple[bool, str]:
    if not source.exists():
        return False, f"PDF not found: {source}"
    with source.open("rb") as handle:
        header = handle.read(4)
    if header != b"%PDF":
        return False, "Uploaded file does not look like a PDF."
    try:
        if source.resolve() == destination.resolve():
            return True, ""
    except OSError:
        pass
    shutil.copy2(source, destination)
    return True, ""


def _metadata_for_paper(
    paper: Dict[str, Any],
    papis_id: str,
    files: list[str],
    notes_filename: str,
    status: str,
) -> Dict[str, Any]:
    tags = []
    for group in (paper.get("task_labels") or [], paper.get("target_labels") or [], paper.get("sources") or []):
        for label in group if isinstance(group, list) else []:
            tag = _sanitize_part(label, "")
            if tag and tag not in tags:
                tags.append(tag)
    tags.append("pdf-attached" if files else "pdf-missing")
    summary = paper.get("llm_summary") if isinstance(paper.get("llm_summary"), dict) else {}
    return {
        "papis_id": papis_id,
        "ref": papis_id,
        "title": paper.get("title", ""),
        "author": paper.get("authors") or [],
        "year": paper.get("year") or "",
        "doi": paper.get("doi", ""),
        "url": paper.get("url", ""),
        "abstract": paper.get("abstract", ""),
        "tags": tags,
        "files": files,
        "notes": notes_filename,
        "papersearch_id": paper.get("id", ""),
        "papersearch_source_id": paper.get("source_id", ""),
        "papersearch_sources": paper.get("sources") or [],
        "papersearch_task_labels": paper.get("task_labels") or [],
        "papersearch_target_labels": paper.get("target_labels") or [],
        "papersearch_saved_at": db.utc_now(),
        "papersearch_relevance_score": paper.get("relevance_score", 0),
        "papersearch_pdf_url": paper.get("pdf_url", ""),
        "papersearch_publisher_pdf_url": paper.get("publisher_pdf_url", ""),
        "papersearch_status": status,
        "papersearch_llm_summary": summary.get("summary_zh", ""),
    }


def _write_notes(folder: Path, paper: Dict[str, Any], notes_filename: str) -> None:
    notes = folder / notes_filename
    if notes.exists():
        return
    summary = paper.get("llm_summary") if isinstance(paper.get("llm_summary"), dict) else {}
    body = [
        f"# {paper.get('title', 'Untitled')}",
        "",
        "## PaperSearch Summary",
        "",
        summary.get("summary_zh") or "_No LLM summary yet._",
        "",
        "## Notes",
        "",
        "- ",
    ]
    notes.write_text("\n".join(body), encoding="utf-8")


def _git_sync(config: Dict[str, Any], library_path: Path, message: str) -> Dict[str, Any]:
    cfg = _cfg(config)
    _configure_git_transport(config, library_path)
    if not cfg.get("auto_commit", True):
        return {"committed": False, "pushed": False, "message": "auto_commit disabled"}
    code, status = _run(["git", "status", "--porcelain"], cwd=library_path)
    if code != 0:
        return {"committed": False, "pushed": False, "error": status}
    committed = False
    if status:
        _run(["git", "add", "."], cwd=library_path)
        code, output = _run(["git", "commit", "-m", message], cwd=library_path)
        if code != 0 and "nothing to commit" not in output.lower():
            return {"committed": False, "pushed": False, "error": output}
        committed = code == 0
    code, remotes = _run(["git", "remote"], cwd=library_path)
    if not cfg.get("auto_push", True) or code != 0 or "origin" not in remotes.split():
        return {"committed": committed, "pushed": False, "message": "push pending: no origin remote or auto_push disabled"}
    code, output = _run(["git", "push", "origin", "HEAD"], cwd=library_path, timeout=120)
    return {"committed": committed, "pushed": code == 0, "error": "" if code == 0 else output}


def sync_paper_to_papis(
    conn: Any,
    paper_id: int,
    config: Dict[str, Any],
    pdf_path: str | None = None,
) -> Dict[str, Any]:
    if not _enabled(config):
        return {"ok": False, "status": "disabled", "error": "Papis integration disabled."}
    paper = db.get_paper(conn, paper_id)
    if not paper:
        return {"ok": False, "status": "error", "error": "Paper not found."}
    configure_network(config.get("network", {}))
    ensure_papis_library(config)
    library_path = papis_library_path(config)
    folder = _find_existing_folder(library_path, paper) or library_path / "documents" / _folder_for_paper(config, paper)
    folder.mkdir(parents=True, exist_ok=True)
    papis_id = str(paper.get("papis_id") or f"ps-{paper_id}-{_slug(str(paper.get('title', 'paper')))}")

    files: list[str] = []
    pdf_error = ""
    target_pdf = folder / f"{_slug(str(paper.get('title', 'paper')))}.pdf"
    if pdf_path:
        ok, pdf_error = _copy_uploaded_pdf(resolve_path(pdf_path), target_pdf)
        if ok:
            files.append(target_pdf.name)
    elif paper.get("local_pdf_path") and Path(str(paper["local_pdf_path"])).exists():
        ok, pdf_error = _copy_uploaded_pdf(Path(str(paper["local_pdf_path"])), target_pdf)
        if ok:
            files.append(target_pdf.name)
    elif paper.get("pdf_url"):
        ok, pdf_error = _download_pdf(
            str(paper["pdf_url"]),
            target_pdf,
            int(_cfg(config).get("download_timeout_seconds", 20) or 20),
            allow_insecure_tls=bool(_cfg(config).get("download_allow_insecure_tls", False)),
        )
        if ok:
            files.append(target_pdf.name)
    elif paper.get("publisher_pdf_url"):
        ok, pdf_error = _download_pdf(
            str(paper["publisher_pdf_url"]),
            target_pdf,
            int(_cfg(config).get("download_timeout_seconds", 20) or 20),
            allow_insecure_tls=bool(_cfg(config).get("download_allow_insecure_tls", False)),
        )
        if ok:
            files.append(target_pdf.name)

    status = "synced" if files else "pending_pdf"
    notes_filename = _notes_filename(str(paper.get("title", "") or "Untitled"))
    metadata = _metadata_for_paper(paper, papis_id, files, notes_filename, status)
    _write_yaml(folder / "info.yaml", metadata)
    _write_notes(folder, paper, notes_filename)
    git_result = _git_sync(config, library_path, f"Archive paper {paper_id}: {paper.get('title', '')[:80]}")
    error_parts = [part for part in [pdf_error if not files else "", git_result.get("error", "")] if part]
    db.update_papis_metadata(
        conn,
        paper_id,
        papis_status=status,
        papis_id=papis_id,
        papis_folder=str(folder),
        papis_error="; ".join(error_parts),
        local_pdf_path=str(target_pdf) if files else "",
    )
    return {
        "ok": True,
        "status": status,
        "papis_id": papis_id,
        "folder": str(folder),
        "files": files,
        "pdf_error": pdf_error if not files else "",
        "git": git_result,
    }


def sync_saved_papers(config_path: str | None = None, limit: int | None = None) -> Dict[str, Any]:
    config = load_config(config_path)
    db.init_db(config["storage"]["database_path_resolved"])
    summary = {"checked": 0, "synced": 0, "pending_pdf": 0, "errors": 0, "items": []}
    with db.connect(config["storage"]["database_path_resolved"]) as conn:
        papers = db.list_papers(conn, status="saved", limit=limit or 1000)
        for paper in papers:
            summary["checked"] += 1
            try:
                result = sync_paper_to_papis(conn, int(paper["id"]), config)
                if result.get("status") == "synced":
                    summary["synced"] += 1
                elif result.get("status") == "pending_pdf":
                    summary["pending_pdf"] += 1
                else:
                    summary["errors"] += 1
                summary["items"].append({"paper_id": paper["id"], **result})
            except Exception as exc:  # noqa: BLE001
                db.update_papis_metadata(conn, int(paper["id"]), papis_status="error", papis_error=str(exc))
                summary["errors"] += 1
                summary["items"].append({"paper_id": paper["id"], "ok": False, "error": str(exc)})
    return summary


def attach_pdf_to_paper(config_path: str | None, paper_id: int, pdf_path: str) -> Dict[str, Any]:
    config = load_config(config_path)
    db.init_db(config["storage"]["database_path_resolved"])
    with db.connect(config["storage"]["database_path_resolved"]) as conn:
        return sync_paper_to_papis(conn, paper_id, config, pdf_path=pdf_path)


def papis_status(config_path: str | None = None) -> Dict[str, Any]:
    config = load_config(config_path)
    library_path = papis_library_path(config)
    git_status = ""
    remote = ""
    if library_path.exists():
        _, git_status = _run(["git", "status", "--short"], cwd=library_path)
        _, remote = _run(["git", "remote", "-v"], cwd=library_path)
    counts = {"synced": 0, "pending_pdf": 0, "error": 0, "not_exported": 0}
    db_path = config["storage"]["database_path_resolved"]
    if Path(db_path).exists():
        with db.connect(db_path) as conn:
            for paper in db.list_papers(conn, status="saved", limit=1000):
                status = str(paper.get("papis_status") or "not_exported")
                counts[status] = counts.get(status, 0) + 1
    return {
        "enabled": _enabled(config),
        "library_path": str(library_path),
        "library_exists": library_path.exists(),
        "papis_available": _tool_exists("papis"),
        "git_lfs_available": _tool_exists("git-lfs"),
        "remote": remote,
        "git_status": git_status,
        "counts": counts,
    }


def init_papis(config_path: str | None = None, install_tools: bool = False, remote_url: str = "") -> Dict[str, Any]:
    config = load_config(config_path)
    if remote_url:
        config.setdefault("papis", {})["remote_url"] = remote_url
    result = ensure_papis_library(config, install_tools=install_tools)
    result.update(papis_status(config_path))
    return result
