---
name: papis-paper-library
description: Locate and inspect the user's PaperSearch-managed Papis paper library; search papers by title, DOI, tag, venue, or PaperSearch metadata; read info.yaml, attached PDFs, and notes for downstream analysis.
---

# Papis Paper Library

Use this skill when the user asks to find, inspect, open, read, or summarize papers stored in the PaperSearch Papis library.

## Library

- Default path: `~/Library/Application Support/PaperSearch/papis-library`
- Convenience symlink: `~/PaperSearchPapis`
- Papers live under `documents/**/info.yaml`; attached PDFs are listed in each `info.yaml` `files:` field.
- Prefer the default path if both paths exist.

## Workflow

1. Locate the library:
   ```bash
   test -d "$HOME/Library/Application Support/PaperSearch/papis-library" && printf '%s\n' "$HOME/Library/Application Support/PaperSearch/papis-library"
   ```
2. Search metadata with `rg` first:
   ```bash
   rg -i "query|doi|tag" "$LIB/documents" -g info.yaml
   ```
3. Read the matching `info.yaml` and, if useful, the Markdown note named by its `notes` field.
4. To read PDFs, use the paths in `files:` relative to the paper folder. Prefer `pdftotext` if available; otherwise use a local PDF library if one is already present.
5. Report exact paper folder paths when handing work to another tool or session.

## Safety

- Do not edit metadata, notes, PDFs, or git state unless the user explicitly asks.
- If multiple papers match, show the short candidate list and ask which one to inspect unless the user's intent is obvious.
