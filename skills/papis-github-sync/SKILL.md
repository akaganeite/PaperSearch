---
name: papis-github-sync
description: Sync the PaperSearch-managed Papis library with its independent GitHub repository, including Git LFS status, commits for metadata/PDF/notes changes, and push troubleshooting.
---

# Papis GitHub Sync

Use this skill when the user asks to sync, commit, push, publish, back up, or inspect git state for the PaperSearch Papis library.

## Library

- Default path: `~/Library/Application Support/PaperSearch/papis-library`
- PDFs should be tracked through Git LFS via `.gitattributes` entries such as `*.pdf filter=lfs diff=lfs merge=lfs -text`.

## Workflow

1. Enter the library and inspect state:
   ```bash
   cd "$HOME/Library/Application Support/PaperSearch/papis-library"
   git status --short
   git remote -v
   git lfs status
   ```
2. If no `origin` remote exists, ask the user for the independent Papis GitHub repo URL before pushing.
3. Ensure PDF changes are LFS-tracked before committing:
   ```bash
   git lfs track "*.pdf"
   git add .gitattributes
   ```
4. Stage only library changes, then commit with a concise message such as:
   ```bash
   git add .
   git commit -m "Update paper library"
   ```
5. Push with:
   ```bash
   git push origin HEAD
   ```

## Failure Handling

- If push fails because remote is missing, report that local commits are safe and push is pending.
- If Git LFS is missing, install or initialize it only with the user's consent unless the current task explicitly includes setup.
- Do not modify the PaperSearch application repo while syncing the Papis library.
