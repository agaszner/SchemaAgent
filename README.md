# SchemaSage

LLM-based database documentation, schema discovery and text-to-SQL over SQLite.

SchemaSage reads an SQLite database, profiles its data, builds a relationship
graph (declared foreign keys plus inferred candidates you can accept or reject),
and serves all of it through a FastAPI backend and a Streamlit UI.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- `make` (preinstalled on macOS and most Linux distributions)

## Quick start

```bash
make install                               # install dependencies
echo 'OPENAI_API_KEY=sk-...' >> .env       # the Docs step calls the OpenAI API
make dev                                   # start the API and the UI together
```

Put the key in `.env` before `make dev`: the API reads it at startup, and without it the
Docs step of **Run all steps** fails (the other steps still finish).

Then open http://localhost:8501. In **Management**, register `data/chinook.db`
(the sample database in the repo), then press **Run all steps** on the Home page.
Review, edit and export the table descriptions in Management · Documentation.
The **Explorer** page then browses tables and runs read-only queries in its SQL tab.
The **Chat** page answers questions about the database.

`make dev` runs both servers in one terminal and Ctrl-C stops both. To run them
in separate terminals, use `make api` and `make ui`.

## Make targets

| Target | What it does |
|--------|--------------|
| `make install` | Install dependencies (`uv sync`) |
| `make dev` | Start the API and the UI together |
| `make api` | Start the API at http://127.0.0.1:8000, interactive docs at `/docs` |
| `make ui` | Start the Streamlit UI at http://localhost:8501 |
| `make test` | Run the test suite |
| `make data` | Download more sample databases, for example `make data TIER=easy` |
| `make help` | List the targets |

`make data` needs `data/download.sh`, which is not tracked in git.

## Configuration

Set these as environment variables or in a `.env` file at the repo root.

| Variable | Default | Meaning |
|----------|---------|---------|
| `SCHEMASAGE_APP_DB` | `app.db` | SQLite file for the app state (registered databases, runs, relationships) |
| `SCHEMASAGE_DATA_DIR` | `data` | Only databases inside this folder can be registered |
| `SCHEMASAGE_API_URL` | `http://127.0.0.1:8000` | Where the UI finds the API |
| `SCHEMASAGE_EXPORT_DIR` | `docs` | Where Export Markdown writes `tables/<Table>.md` and `schema_overview.md` |
| `OPENAI_API_KEY` | none | OpenAI API key; the Docs step needs it |

The SQL row limit, query timeout, LLM model, reasoning effort and token prices are set in Management · Settings (stored in app.db).

## Command-line tools

```bash
uv run python -m core.parser data/chinook.db    # schema model as JSON
uv run python -m core.profiler data/chinook.db  # data profile as JSON
```

## Project layout

```
core/                 pipeline logic, no HTTP and no UI
  parser.py           DDL to JSON data model
  profiler.py         data profile, masking, FK candidates
  graph.py            relationship graph and JOIN paths
  sqlite.py           read-only connections, identifier quoting
  sql_guard.py        SQL guard: SELECT only, LIMIT, timeout, masking
  documentation.py    LLM table documentation prompts, validation, export
  evaluation.py       benchmark contexts (A, B, C), answer scoring, metrics
  agent.py            chat agent: tool loop, evidence, repair retry
api/                  FastAPI app
  main.py config.py   app and settings
  routes/             HTTP routes: databases, pipeline, relationships, tables, sql, settings, docs, benchmark, chat
  payloads.py         request and response models
  appdb.py            app.db access
  jobs.py             runs pipeline steps in the background
  llm.py              OpenAI client
ui/                   Streamlit UI
  main.py             entry point: theme, sidebar, navigation
  api_client.py       HTTP client for api/
  views/              pages: home, pipeline, explorer, chat, management (+ management_tabs/)
  markup.py           HTML builders and formatters
  widgets.py          Streamlit pieces shared by several pages
  network.py          pyvis relationship graph
  theme.py design/    design tokens
benchmark/            questions.jsonl, gold.sql, results.csv (written by the benchmark run)
tests/                pytest suite mirroring core/, api/ and ui/
data/                 sample databases (only chinook.db is in git)
```

The pipeline runs parse, then profile, then graph, then docs. `api/` calls the
`core/` functions; the UI reaches them only over HTTP. App state lives in `app.db`, separate from the databases
being analysed.

## Evaluation

The benchmark asks the same model the same 40 chinook questions under three contexts:

- **A**: the raw DDL.
- **B**: the generated table docs plus a relationship graph excerpt and JOIN paths.
- **C**: everything in B plus the data profile, sample values and sample rows (masked).

To run it, run all steps on chinook (the Docs step needs the OpenAI key), then press
**Run benchmark (A / B / C)** in Management · Benchmark. It runs in the background and writes one row
per (config, question) to `benchmark/results.csv` (`SCHEMASAGE_BENCHMARK_DIR`).

- **Execution accuracy**: share of answerable data questions whose result set matches the gold query (row order ignored, column names ignored, floats rounded to 2 decimals, extra columns allowed).
- **Valid SQL rate**: share of answerable data questions whose first answer is SQL that passes the guard and runs.
- **Repair success**: over answerable data questions, first-attempt SQL failures the retry fixed, out of those retried.
- **Schema fact accuracy**: over answerable schema questions, mean share of the expected tables and `Table.Column` names the answer names.
- **Path accuracy**: over relationship and path questions, whether the answered JOIN path equals the expected one (either direction).
- **Unsupported claim rate**: tables and columns that schema answers name but that do not exist, out of all they name (SQL answers are measured by the valid SQL rate instead).
- **Abstention accuracy**: whether the model abstained exactly when the question is unanswerable.
- **Mean latency, tokens, cost**: mean wall time per question, and summed tokens and cost per config.

A refused answer counts as wrong.

## Chat

The Chat page (after Explorer) answers questions about the selected database. It needs
parse, profile and graph done (otherwise 409) and `OPENAI_API_KEY` (otherwise 503).

The agent has five tools: `search_tables`, `get_table_doc`, `find_path`, `get_sample_rows`
and `run_sql`. It is a plain tool loop on the OpenAI Responses API (`core/agent.py`, at most
8 rounds, the last 3 turns as history). The spec names the OpenAI Agents SDK; this project does not
use it because the code base is sync (sync OpenAI client, sync routes, a sync fake in tests)
and the SDK expects `AsyncOpenAI`.

- Each answer is one of: data, schema, not enough information, refused, failed.
- The evidence (SQL, result, JOIN steps, assumed relationships, tool calls, names the
  answer mentions that do not exist) comes from the tool trace, not from the model.
- `proposed` relationships are used but flagged as assumed: when `find_path` returns one, or when a
  successful query reads both of its tables. `rejected` ones never reach the model through the docs
  or `find_path`; a join the model writes on its own over a rejected pair is not blocked.
- A failed query gets one repair retry; after a second failure, or if the database itself fails,
  `run_sql` stops running for that turn.
- Write requests are refused, in the instructions and again by the SQL guard.
- The log is kept in app.db and each answer takes a thumbs up or down. Marking a new column clears the log.
- Each answer shows latency, tokens and cost.

## Safety rules

- Analysed databases are always opened read-only.
- Table and column names are quoted, and values are passed as bound parameters.
- Columns that look like personal data are masked (`***`) before any value
  could reach an LLM. Documentation prompts are built only from the masked profile.
- SQL runs only after sqlglot validation: one SELECT over known tables and columns,
  with a row limit and a timeout. Masked columns can only be selected (their values
  come back as `***`), never filtered, joined, sorted or grouped on.
- Declared foreign keys cannot be deleted, only disabled. Rejected relationship
  candidates are never proposed again. Every change to a relationship is logged.
