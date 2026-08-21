---
name: papis-paper-notes
description: Generate or update title-named Markdown notes for papers in the PaperSearch-managed Papis library only when the user explicitly asks to write or update notes; otherwise analyze papers in chat by reading info.yaml, attached PDFs, and existing notes.
---

# Papis Paper Notes

Use this skill when the user asks to analyze a Papis paper, write notes, update notes, extract method details, compare papers, or prepare research notes from a saved PaperSearch paper.

## Persistence Rule

Do not create, update, delete, or rename a paper note unless the user explicitly asks for that file operation. If the user asks to read, summarize, explain, analyze, compare, or extract ideas from a paper, provide the result in chat by default.

## Workflow

1. Find the paper folder in `~/Library/Application Support/PaperSearch/papis-library/documents` by title, DOI, tag, or `papersearch_id`.
2. Read `info.yaml` first for title, authors, abstract, tags, PaperSearch labels, and attached `files:`.
3. Read the PDF from `files:` when present. If no PDF is attached, tell the user that only metadata is available.
4. Resolve the note filename from the `notes` field in `info.yaml`. If the field is absent in legacy metadata, look for the sole Markdown file beside `info.yaml` and prefer `notes.md` when it exists.
5. When the user explicitly asks to create a note and no note file exists, derive `<paper title>.md`, replace filesystem-unsafe characters (`<>:"/\\|?*`) with ` - `, and record that relative filename in the `notes` field of `info.yaml`.
6. If the user explicitly asks to write/update the paper note, preserve its existing content; append or carefully update sections rather than replacing user-written notes.
7. For explicit note-writing requests, use this default structure unless the user asks for another format:
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
8. When possible, mention source page, section, figure, or table references in the chat analysis or requested notes.

## Git

After writing notes, if the library is a git repo, show `git status --short`. Commit and push only when the user asks or when the active task explicitly includes synchronization.
