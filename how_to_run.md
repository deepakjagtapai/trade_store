# How to run trade-store

Step-by-step instructions to run the REST API locally, use the manual testing UI, or run via Docker.

**Requirements:** Python 3.12+, `make`. Optional: Docker.

---

## Option A — Local development (recommended)

### Step 1 — Go to the project

```bash
cd /Users/deepak.jagtap/Downloads/all_projects/trade
```

Adjust the path if your clone lives elsewhere.

### Step 2 — Create and activate a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

You should see `(.venv)` in your shell prompt.

### Step 3 — Install dependencies

```bash
make install
```

This installs runtime and dev dependencies from `requirements-dev.txt`.

### Step 4 — Start the API server

```bash
make run
```

This starts Uvicorn with reload on **http://127.0.0.1:8000**. Leave this terminal open while the app runs.

### Step 5 — Verify it is running

In another terminal (activate the venv if you use the same machine for curl):

```bash
curl http://127.0.0.1:8000/health
```

Or open in a browser:

| URL | Purpose |
|-----|--------|
| http://127.0.0.1:8000/docs | Interactive OpenAPI / Swagger UI |
| http://127.0.0.1:8000/ui | Manual testing console |
| http://127.0.0.1:8000/health | Health check |

On first start, logs should mention the expiry scheduler and an initial expiry sweep. The default database is SQLite at **`./trade_store.db`** in the project directory.

### Step 6 — Stop the server

In the terminal where `make run` is running, press **Ctrl+C**.

---

## Option B — Manual testing UI (demo mode)

For simulated dates, store reset, and guided scenario presets:

```bash
cd /Users/deepak.jagtap/Downloads/all_projects/trade
source .venv/bin/activate
make demo
```

Then open **http://127.0.0.1:8000/ui**.

> **Warning:** Demo mode allows changing the business date and wiping all trades. Use only for local testing — never enable `DEMO_MODE=true` in production.

With `make run` (without demo), `/ui` still works but uses the real calendar date and omits demo-only controls.

---

## Option C — Docker

Build the image:

```bash
cd /Users/deepak.jagtap/Downloads/all_projects/trade
make docker-build
```

Run the API (SQLite data in a Docker volume):

```bash
docker run --rm -p 8000:8000 -v trade-data:/app/data trade-store:latest
```

Open http://localhost:8000/docs.

Demo UI in Docker:

```bash
docker run --rm -p 8000:8000 -e DEMO_MODE=true trade-store:latest
```

Then open http://localhost:8000/ui.

---

## Configuration (optional)

Settings come from environment variables (or a `.env` file if you add one). Common variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite:///./trade_store.db` | SQLAlchemy database URL |
| `ENABLE_SCHEDULER` | `true` | Startup and daily expiry sweep |
| `EXPIRY_CRON_HOUR` | `0` | Hour (0–23, server local time) for daily sweep |
| `LOG_LEVEL` | `INFO` | Logging level for the `app` logger |
| `ENABLE_UI` | `true` | Serve `/ui` and static assets; set `false` to disable |
| `DEMO_MODE` | `false` | Demo endpoints and UI features (`make demo` sets this to `true`) |

Example:

```bash
export LOG_LEVEL=DEBUG
make run
```

See [readme.md](readme.md#configuration) for full details.

---

## Quick API smoke test

With the server running (`make run`):

```bash
curl -X POST http://127.0.0.1:8000/trades \
  -H "Content-Type: application/json" \
  -d '{
    "trade_id": "T1",
    "version": 1,
    "counterparty_id": "C1",
    "portfolio_id": "P1",
    "maturity_date": "2030-12-31"
  }'
```

Use a maturity date that is **today or in the future**; past dates are rejected with `MATURITY_IN_PAST`.

---

## Run tests (optional)

With the virtual environment activated:

```bash
make test
```

Other useful targets: `make lint`, `make typecheck`, `make regression`. See [readme.md](readme.md#make-targets).

---

## Troubleshooting

| Problem | What to try |
|---------|-------------|
| `command not found: uvicorn` | Activate `.venv` and run `make install` again. |
| Port 8000 already in use | Stop the other process, or run on another port: `python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8001` |
| `make` cannot find Python | Use the same `python3` you used to create the venv. |

For architecture, API reference, and business rules, see [readme.md](readme.md).
