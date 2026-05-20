# PaperSearch

Local paper radar for daily incremental discovery, triage, PDF lookup, and saved-paper summaries.

PaperSearch is intentionally lightweight: Python standard library, SQLite, and a small browser UI served by a local backend. It can fetch from arXiv, csPapers, and configured venue accepted-paper/preprint pages.

## Quick Start

Create a local config from the template:

```bash
cp configs/local.example.json configs/local.json
```

Edit `configs/local.json`:

- Set `pdf_resolver.unpaywall_email` to your email.
- Keep `llm_summary.api_key_env` as an environment variable name, for example `DEEPSEEK_API_KEY`.
- Set `profile_path` to a profile JSON. The included sample is `profiles/software_security_llm_agent.sample.json`.

Set your LLM API key in the environment:

```bash
export DEEPSEEK_API_KEY="your-key"
```

Initialize and run:

```bash
python3 -m papersearch config-check
python3 -m papersearch init-db
python3 -m papersearch update --bootstrap
python3 -m papersearch serve --port 8765
```

Then open:

```text
http://127.0.0.1:8765
```

## Profiles

Interests are configured through profile JSON files. A profile defines:

- paper topics and hard excludes
- arXiv categories and csPapers venues
- top-venue exception rules
- task labels and target labels
- summary focus text for saved-paper LLM summaries

The bundled `profiles/software_security_llm_agent.sample.json` is a sample profile for LLM/Agent applications in software security. Copy it and edit your own version when sharing or adapting the tool:

```bash
cp profiles/software_security_llm_agent.sample.json profiles/my_profile.json
```

Then point `configs/local.json` at your profile:

```json
{
  "profile_path": "profiles/my_profile.json"
}
```

## Commands

```bash
python3 -m papersearch config-check
python3 -m papersearch init-db
python3 -m papersearch update --bootstrap
python3 -m papersearch update
python3 -m papersearch resolve-pdfs --limit 50
python3 -m papersearch summarize-saved --limit 10
python3 -m papersearch serve --port 8765
python3 -m unittest discover -s tests
```

The web server includes a lightweight daily scheduler. It runs the update once per configured local day while the server process is alive. The UI also has buttons for immediate paper updates and saved-paper summaries.

## Private Files

Do not commit local state or credentials. The `.gitignore` excludes:

- `configs/local.json`
- `configs/interests.json` legacy local config
- `.env`
- `data/*.sqlite*`
- Python caches and local logs
- local pet/artifact folders

If a secret was ever committed to a branch that might be pushed, rotate that key and publish from a clean branch without that history.
