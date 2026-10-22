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
uv run python -m src.parser data/chinook.db     # schema model as JSON
uv run python -m src.profiler data/chinook.db   # data profile as JSON
```

## Project layout

```
src/        pipeline logic: parser, profiler, schema graph, SQL helpers
backend/    FastAPI app: HTTP routes, app.db storage, run orchestration
app/        Streamlit UI
tests/      pytest suite, run against real SQLite files
data/       sample databases (only chinook.db is in git)
```

The pipeline runs parse, then profile, then graph. `backend/` and the UI call the
same `src/` functions. App state lives in `app.db`, separate from the databases
being analysed.

## Safety rules

- Analysed databases are always opened read-only.
- Table and column names are quoted, and values are passed as bound parameters.
- Columns that look like personal data are masked (`***`) before any value
  could reach an LLM.
- Declared foreign keys cannot be deleted, only disabled. Rejected relationship
  candidates are never proposed again. Every change to a relationship is logged.
