---
name: papis-paper-notes
description: Generate or update notes.md for papers in the PaperSearch-managed Papis library by reading info.yaml, attached PDFs, and existing notes; useful for paper analysis, literature notes, method summaries, limitations, and follow-up questions.
---

# Papis Paper Notes

Use this skill when the user asks to analyze a Papis paper, write notes, update notes, extract method details, compare papers, or prepare research notes from a saved PaperSearch paper.

## Workflow

1. Find the paper folder in `~/Library/Application Support/PaperSearch/papis-library/documents` by title, DOI, tag, or `papersearch_id`.
2. Read `info.yaml` first for title, authors, abstract, tags, PaperSearch labels, and attached `files:`.
3. Read the PDF from `files:` when present. If no PDF is attached, tell the user that only metadata is available.
4. Preserve any existing `notes.md`; append or carefully update sections rather than replacing user-written notes.
5. Use this default structure unless the user asks for another format:
   ```markdown
   # Notes

   ## One-paragraph Summary
   ## Problem and Motivation
   ## Method
   ## Key Claims
   ## Evidence
   ## Limitations
   ## Connections
   ## Questions to Verify
   ```
6. When possible, mention source page, section, figure, or table references in the notes.

## Git

After writing notes, if the library is a git repo, show `git status --short`. Commit and push only when the user asks or when the active task explicitly includes synchronization.
