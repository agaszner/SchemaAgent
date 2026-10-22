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
make install   # install dependencies
make dev       # start the API and the UI together
```

Then open http://localhost:8501. In **Management**, register `data/chinook.db`
(the sample database in the repo), then press **Run all steps** on the Home page.

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
api/                  FastAPI app
  main.py config.py   app and settings
  routes/             HTTP routes: databases, pipeline, relationships
  payloads.py         request and response models
  appdb.py            app.db access
  jobs.py             runs pipeline steps in the background
ui/                   Streamlit UI
  main.py             entry point: theme, sidebar, navigation
  api_client.py       HTTP client for api/
  views/              pages: home, pipeline, management (+ management_tabs/)
  markup.py           HTML builders and formatters
  widgets.py          Streamlit pieces shared by several pages
  network.py          pyvis relationship graph
  theme.py design/    design tokens
tests/                pytest suite mirroring core/, api/ and ui/
data/                 sample databases (only chinook.db is in git)
```

The pipeline runs parse, then profile, then graph. `api/` calls the
`core/` functions; the UI reaches them only over HTTP. App state lives in `app.db`, separate from the databases
being analysed.

## Safety rules

- Analysed databases are always opened read-only.
- Table and column names are quoted, and values are passed as bound parameters.
- Columns that look like personal data are masked (`***`) before any value
  could reach an LLM.
- Declared foreign keys cannot be deleted, only disabled. Rejected relationship
  candidates are never proposed again. Every change to a relationship is logged.
