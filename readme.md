# trade-store

[![CI](https://github.com/deepakjagtapai/trade_store/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/deepakjagtapai/trade_store/actions/workflows/ci.yml)

A REST service that stores trades and enforces versioning, maturity and expiry rules.
Built with FastAPI, Pydantic v2, SQLAlchemy 2.x and APScheduler, developed test-first.

- [Business rules](#business-rules)
- [Design decisions](#design-decisions)
- [Setup and running](#setup-and-running)
- [Manual testing UI](#manual-testing-ui)
- [API reference](#api-reference)
- [Testing strategy](#testing-strategy)
- [CI/CD pipeline](#cicd-pipeline)
- [Vulnerability scanning policy](#vulnerability-scanning-policy)
- [Design diagrams](#design-diagrams)

---

## Overview

Thousands of trades are sent to a single store. Each trade is identified by
`(trade_id, version)` and carries a counterparty, a portfolio, and a maturity date. The
store keeps the full version history of every trade, rejects stale or already-matured
trades, and flags stored trades as expired once their maturity date has passed.

| Layer       | Module                         | Responsibility                                          |
|-------------|--------------------------------|---------------------------------------------------------|
| API         | `app/api/routes.py`            | HTTP endpoints, request/response models                 |
| Wiring      | `app/main.py`, `dependencies.py` | App factory, error mapping, lifespan, DI              |
| Service     | `app/service.py`               | **All** business rules and transaction boundaries       |
| Repository  | `app/repository.py`            | Data access only, never commits                         |
| Time        | `app/clock.py`                 | The only module allowed to read the wall clock          |
| Scheduler   | `app/scheduler.py`             | Startup + daily expiry sweep                            |

## Business rules

| # | Rule | Implementation |
|---|------|----------------|
| 1 | **Version.** A lower version than the highest stored version for that `trade_id` is rejected. The same version replaces the stored record. A higher version is stored as a new record. | `TradeService.store_trade` locks the trade's rows, reads `max(version)`, and raises `LowerVersionError` (HTTP 409) if `incoming < max`. Otherwise it updates the existing `(trade_id, version)` row or inserts a new one. Comparing against the *max* means a gap is still rejected (stored v3, incoming v2 with no v2 in DB → 409). |
| 2 | **Maturity.** A trade whose `maturity_date` is earlier than today is rejected. Today itself is allowed. | `store_trade` checks `maturity_date < clock.today()` first, before any DB access, and raises `MaturityInPastError` (HTTP 400). |
| 3 | **Expiry.** When a stored trade's `maturity_date < today`, it is automatically flagged `expired = True`. | `TradeRepository.mark_expired` runs one bulk `UPDATE trades SET expired = true WHERE maturity_date < :today AND expired = false`. It is triggered by APScheduler (once at startup, then daily at `EXPIRY_CRON_HOUR`) and on demand by `POST /admin/expire-trades`. |

`store_trade` runs these steps in order, inside one transaction:

1. `today = clock.today()`
2. maturity check (rule 2)
3. `SELECT ... FOR UPDATE` on the trade's rows, then `SELECT max(version)`
4. version check (rule 1)
5. same version → overwrite `counterparty_id`, `portfolio_id`, `maturity_date`; set `created_date = today`, `expired = False`
6. new version → insert with `created_date = today`, `expired = False`
7. commit; roll back on any exception

## Design decisions

- **Composite primary key `(trade_id, version)` plus version history.** Every version is its
  own row, so `GET /trades/{id}/versions` gives a full audit trail and "latest" is just the
  max version. The DB also enforces `version >= 1` with a `CHECK` constraint, and indexes
  `trade_id`, `maturity_date` and `expired` for lookups and the expiry sweep.
- **Injected `Clock`.** Every rule gets "today" from a `Clock` (`SystemClock` in production,
  `FixedClock` in tests). That makes every date rule deterministic and lets tests move time
  forward with `FixedClock.set()`. Two checks enforce that no module except `clock.py` reads
  the wall clock: ruff's `DTZ` rules and an AST test (`test_no_module_other_than_clock_reads_the_wall_clock`).
- **Two expiry mechanisms.** The scheduled sweep makes expiry automatic. Running it at
  startup also catches up after downtime, so a missed midnight run is fixed on the next boot.
  The manual endpoint gives operators and tests a deterministic trigger. Both call the same
  `TradeService.expire_matured_trades()`, which is idempotent: a second run marks 0 rows.
- **Status codes.** A past maturity date is a bad request (**400**): the payload can never be
  valid. A lower version is a **409 Conflict** with the server's current state: the client
  is out of date, and the response says which version is current. Unknown trades return
  **404**. Schema violations keep FastAPI's **422**. Every domain error uses the same
  `{"error_code", "message"}` body.
- **Why maturity today is allowed.** A trade maturing today is still live for the whole day.
  It only becomes expired once `maturity_date < today`. Rule 2 (reject) and rule 3 (expire)
  use the same strict `<`, so a trade can never be accepted and immediately expired.
- **Server-owned fields.** `created_date` and `expired` are not part of `TradeIn`, and
  `extra="forbid"` turns any attempt to send them into a 422.
- **Concurrency.** On PostgreSQL, `SELECT ... FOR UPDATE` serialises writers for the same
  `trade_id`. The max version is then read in a *separate* statement, so under READ
  COMMITTED it sees versions committed by whoever held the lock first. If two requests
  insert the same new key at once, the `IntegrityError` is caught, the transaction rolled
  back, and the whole check-and-write is retried once (where it becomes a same-version
  replace). On SQLite, `FOR UPDATE` is not emitted and SQLite's database-level write lock
  serialises writers. One known limitation: a brand-new `trade_id` has no rows to lock, so
  two *different* first versions arriving at the same instant are not ordered. Closing that
  gap would need an advisory lock or a parent `trade_ids` table.
- **Portable database.** `DATABASE_URL` selects the database. SQLite is the default, and
  PostgreSQL works without code changes (install a driver, e.g. `pip install "psycopg[binary]"`,
  and set `DATABASE_URL=postgresql+psycopg://user:pass@host:5432/trades`). Tables are created
  at startup with `create_all`. Alembic migrations are the natural next step for schema evolution.
- **Structured logging.** The `app` logger writes one JSON object per line. Every accepted or
  rejected trade is logged with `trade_id`, `version` and `outcome` (`inserted`, `replaced`,
  `rejected`, plus `error_code`), and every sweep with `run_date` and `expired_count`.

## Setup and running

Requires Python 3.12+ (CI uses 3.12).

```bash
python3 -m venv .venv
source .venv/bin/activate
make install            # pip install -r requirements-dev.txt
make run                # uvicorn app.main:app --reload on http://127.0.0.1:8000
```

Then open <http://127.0.0.1:8000/docs> for the interactive OpenAPI UI. On startup the log shows:

```json
{"level": "INFO", "logger": "app.scheduler", "message": "Expiry scheduler started: startup sweep queued, daily sweep at 00:00"}
{"level": "INFO", "logger": "app.service", "message": "Expiry sweep for 2026-10-19 marked 0 trade(s) expired", "run_date": "2026-10-19", "expired_count": 0}
```

### Configuration

All settings come from environment variables, or from a `.env` file
(`cp .env.example .env` for a documented starting point):

| Variable           | Default                      | Description                               |
|--------------------|------------------------------|-------------------------------------------|
| `DATABASE_URL`     | `sqlite:///./trade_store.db` | Any SQLAlchemy URL                        |
| `ENABLE_SCHEDULER` | `true`                       | Run the startup + daily expiry sweep      |
| `EXPIRY_CRON_HOUR` | `0`                          | Hour (0-23, server local time) of the daily sweep |
| `LOG_LEVEL`        | `INFO`                       | Level for the `app` logger                |
| `ENABLE_UI`        | `true`                       | Serve the manual-testing console at `/ui` and its files at `/static` |
| `DEMO_MODE`        | `false`                      | Enable the simulated-date and reset endpoints (see [Manual testing UI](#manual-testing-ui)). **Never enable in production.** |

### Docker

```bash
make docker-build                        # docker build -t trade-store:latest .
docker run --rm -p 8000:8000 -v trade-data:/app/data trade-store:latest
```

The image is a multi-stage build on `python:3.12-slim`. It contains only runtime
dependencies, runs as the non-root user `app` (uid 10001), and has a `HEALTHCHECK` on
`/health`. SQLite data lives in `/app/data`. The image sets `DEMO_MODE=false`; see
[Manual testing UI](#manual-testing-ui) to run a demo container.

### Make targets

| Target          | What it does                                                    |
|-----------------|-----------------------------------------------------------------|
| `install`       | Install runtime + dev dependencies                              |
| `install-e2e`   | Also install Playwright for the browser tests                   |
| `lint`          | `ruff check` + `ruff format --check`                            |
| `format`        | Auto-fix lint and formatting                                    |
| `typecheck`     | `mypy app` (strict)                                             |
| `test`          | Unit + API tests with coverage (fails under 90%)                |
| `regression`    | `pytest -m regression`                                          |
| `e2e`           | Browser smoke tests for `/ui` (`pytest -m e2e --run-e2e`, needs `install-e2e` and Chrome) |
| `run`           | Dev server with reload                                          |
| `demo`          | Dev server with reload and `DEMO_MODE=true`                     |
| `scan`          | `pip-audit -r requirements.txt` and `trivy fs --severity CRITICAL --exit-code 1 .` |
| `diagrams`      | Render `docs/*.puml` to PNG and SVG (`PLANTUML="java -jar plantuml.jar"` to override) |
| `docker-build`  | Build the image                                                 |

## Manual testing UI

A single-page console at **`/ui`** lets anyone paste trades as JSON, submit them, and see
the outcome of each trade plus the current store, laid out like the assignment's sample
table. It's plain HTML/CSS/JS served by the same FastAPI app, with no build step, no Node.js
and no CDN, so it works offline and on a corporate network.

> **Warning: demo mode must never be enabled in production.** It lets anyone change the
> business date and delete every stored trade. The app logs a WARNING at startup whenever
> `DEMO_MODE=true`.

### Starting it

```bash
source .venv/bin/activate
make demo                      # = DEMO_MODE=true uvicorn app.main:app --reload
# then open http://localhost:8000/ui
```

Docker equivalent:

```bash
make docker-build
docker run --rm -p 8000:8000 -e DEMO_MODE=true trade-store:latest
# then open http://localhost:8000/ui
```

`make run` (or the image without `-e DEMO_MODE=true`) also serves `/ui`, but without the
date bar, reset button or guided demo. The rules then use the real date, and a note says so.
Set `ENABLE_UI=false` to stop serving the page entirely (`/`, `/ui`, `/static` and
`/config/ui` return 404).

![Manual test console after the guided demo](docs/ui-console-light.png)

<details><summary>Dark mode</summary>

![Manual test console in dark mode](docs/ui-console-dark.png)

</details>

### What's on the page

- **Simulated date bar** (demo mode only): shows "today" with a *Simulated* or *Real* badge,
  plus a date picker, **Set date** and **Use real date**. The API, `/admin/expire-trades`
  and the scheduler all read the same thread-safe `SwitchableClock`, so a simulated date
  applies everywhere.
- **Input**: scenario presets, a JSON textarea (one object or an array), **Submit**,
  **Format JSON** and **Clear**. Invalid JSON is reported in the browser and nothing is sent.
  An array is submitted **sequentially, in array order**, and every outcome is recorded even
  after a rejection. In demo mode, **Start from an empty store** resets the store just before
  the submission. Presets that need a clean store tick it for you.
- **Results**: `#`, Trade Id, Version, Outcome (Accepted/Rejected), HTTP, Error Code and
  Message. 422 responses are summarised as `field: message`. When the textarea still holds an
  unmodified preset, the summary says whether the outcome matches the preset's expectation.
- **Trade Store**: the sample table's columns, `DD/MM/YYYY` dates, `Y`/`N` expired flag
  (expired rows are greyed out), sorted by Trade Id ascending then Version descending. It also
  has an All/Active/Expired filter, **Refresh**, **Run expiry sweep** and **Reset store**
  (demo mode only, asks for confirmation).

### Scenario presets

The presets live in [`app/static/scenarios.json`](app/static/scenarios.json).
`tests/api/test_demo_scenarios.py` replays every one through the API, so the expected
outcomes below are tested, not just documented.

| # | Scenario | Simulated date | Expected outcome |
|---|----------|----------------|------------------|
| 1 | Sample table as-is (real date) | cleared | All four sample rows rejected with `MATURITY_IN_PAST` |
| 2 | Version rule – lower version rejected | 15/03/2015, empty store | T2 v2 accepted, then T2 v1 rejected with `LOWER_VERSION` (409) |
| 3 | Version rule – same version replaces | 15/03/2015, empty store | Both accepted; one T2 v2 row with portfolio B2 |
| 4 | Version rule – higher version adds history | 14/03/2015, empty store | T2 v1 and T2 v2 both stored as separate rows |
| 5 | Maturity rule | 15/03/2015 | T3 v3 (matures 20/05/2014) rejected with `MATURITY_IN_PAST` |
| 6 | Expiry rule – step 1 | 01/05/2014, empty store | T3 v3 accepted, Expired = N |
| 7 | Expiry rule – step 2 | 21/05/2014 | Nothing to submit; click **Run expiry sweep** → 1 trade expired, T3 shows Expired = Y |
| 8 | Rebuild the sample table (guided demo) | several | Click **Run guided demo** (demo mode only). It resets the store, then stores T3 (01/05/2014), T2 v1 (14/03/2015), T1 v1 and T2 v2 (15/03/2015). A resubmitted T2 v1 is rejected with `LOWER_VERSION`, and the sweep expires T3. The store ends up as the sample table, with only T3 expired. |

Without demo mode, presets still load their JSON, but a note says the date could not be
simulated, so results will differ from the table above.

### Demo-mode API

These routes exist only when `DEMO_MODE=true`. Otherwise they return 404 and are left out
of the OpenAPI schema.

| Method | Path            | Body / response |
|--------|-----------------|-----------------|
| GET    | `/admin/clock`  | → `{"today": "2015-03-14", "overridden": true, "demo_mode": true}` |
| PUT    | `/admin/clock`  | `{"today": "2015-03-14"}` or `{"today": "14/03/2015"}` → same shape; invalid date → 422 |
| DELETE | `/admin/clock`  | Clears the override → same shape with the real date |
| DELETE | `/admin/trades` | Deletes every trade → `{"deleted_count": 4}` |

`GET /config/ui` → `{"demo_mode": false}` is available whenever the UI is enabled.

```bash
curl -X PUT http://localhost:8000/admin/clock -H 'Content-Type: application/json' -d '{"today":"14/03/2015"}'
curl -X DELETE http://localhost:8000/admin/trades
```

Every clock change and reset is logged at WARNING level.

## API reference

All error responses share this body:

```json
{"error_code": "LOWER_VERSION", "message": "..."}
```

| Method | Path                          | Success | Errors        |
|--------|-------------------------------|---------|---------------|
| POST   | `/trades`                     | 201     | 400, 409, 422 |
| GET    | `/trades?expired=true\|false` | 200     | 422           |
| GET    | `/trades/{trade_id}`          | 200     | 404           |
| GET    | `/trades/{trade_id}/versions` | 200     | 404           |
| POST   | `/admin/expire-trades`        | 200     |               |
| GET    | `/health`                     | 200     |               |

### `POST /trades`: store a trade

`maturity_date` accepts ISO `YYYY-MM-DD` or `DD/MM/YYYY`.

```bash
curl -X POST http://127.0.0.1:8000/trades -H 'Content-Type: application/json' \
  -d '{"trade_id":"T1","version":1,"counterparty_id":"CP-1","portfolio_id":"B1","maturity_date":"2030-05-20"}'
```

```json
201 Created
{"trade_id":"T1","version":1,"counterparty_id":"CP-1","portfolio_id":"B1","maturity_date":"2030-05-20","created_date":"2026-10-19","expired":false}
```

Higher version (stored as a new record; `DD/MM/YYYY` input):

```bash
curl -X POST http://127.0.0.1:8000/trades -H 'Content-Type: application/json' \
  -d '{"trade_id":"T1","version":2,"counterparty_id":"CP-2","portfolio_id":"B1","maturity_date":"20/05/2031"}'
```

```json
201 Created
{"trade_id":"T1","version":2,"counterparty_id":"CP-2","portfolio_id":"B1","maturity_date":"2031-05-20","created_date":"2026-10-19","expired":false}
```

Lower version → **409**:

```bash
curl -X POST http://127.0.0.1:8000/trades -H 'Content-Type: application/json' \
  -d '{"trade_id":"T1","version":1,"counterparty_id":"CP-1","portfolio_id":"B1","maturity_date":"2030-05-20"}'
```

```json
409 Conflict
{"error_code":"LOWER_VERSION","message":"Trade 'T1' version 1 is lower than the current maximum stored version 2"}
```

Maturity in the past → **400**:

```bash
curl -X POST http://127.0.0.1:8000/trades -H 'Content-Type: application/json' \
  -d '{"trade_id":"T3","version":3,"counterparty_id":"CP-3","portfolio_id":"B2","maturity_date":"20/05/2014"}'
```

```json
400 Bad Request
{"error_code":"MATURITY_IN_PAST","message":"Maturity date 2014-05-20 is earlier than today (2026-10-19)"}
```

Invalid payload (version < 1, client tries to set `expired`) → **422**:

```bash
curl -X POST http://127.0.0.1:8000/trades -H 'Content-Type: application/json' \
  -d '{"trade_id":"T4","version":0,"counterparty_id":"CP-1","portfolio_id":"B1","maturity_date":"2030-05-20","expired":true}'
```

```json
422 Unprocessable Content
{"detail":[
  {"type":"greater_than_equal","loc":["body","version"],"msg":"Input should be greater than or equal to 1","input":0,"ctx":{"ge":1}},
  {"type":"extra_forbidden","loc":["body","expired"],"msg":"Extra inputs are not permitted","input":true}
]}
```

### `GET /trades`: list trades

```bash
curl http://127.0.0.1:8000/trades
curl 'http://127.0.0.1:8000/trades?expired=true'
```

```json
200 OK
[{"trade_id":"T1","version":1,"counterparty_id":"CP-1","portfolio_id":"B1","maturity_date":"2030-05-20","created_date":"2026-10-19","expired":false},
 {"trade_id":"T1","version":2,"counterparty_id":"CP-2","portfolio_id":"B1","maturity_date":"2031-05-20","created_date":"2026-10-19","expired":false}]
```

### `GET /trades/{trade_id}`: latest version

```bash
curl http://127.0.0.1:8000/trades/T1
```

```json
200 OK
{"trade_id":"T1","version":2,"counterparty_id":"CP-2","portfolio_id":"B1","maturity_date":"2031-05-20","created_date":"2026-10-19","expired":false}
```

```bash
curl http://127.0.0.1:8000/trades/T9
```

```json
404 Not Found
{"error_code":"TRADE_NOT_FOUND","message":"Trade 'T9' not found"}
```

### `GET /trades/{trade_id}/versions`: version history (ascending)

```bash
curl http://127.0.0.1:8000/trades/T1/versions
```

```json
200 OK
[{"trade_id":"T1","version":1,...}, {"trade_id":"T1","version":2,...}]
```

Unknown trade → `404 {"error_code":"TRADE_NOT_FOUND", ...}`.

### `POST /admin/expire-trades`: run the expiry sweep now

```bash
curl -X POST http://127.0.0.1:8000/admin/expire-trades
```

```json
200 OK
{"expired_count":0,"run_date":"2026-10-19"}
```

### `GET /health`

```bash
curl http://127.0.0.1:8000/health
```

```json
200 OK
{"status":"ok"}
```

## Testing strategy

The project was built with strict TDD. Every phase has a `test:` commit with failing
tests followed by a `feat:` commit that makes them pass (see `git log`).

| Suite        | Location                 | What it covers |
|--------------|--------------------------|----------------|
| Unit         | `tests/unit/`            | Clock, schemas, config, logging, DB engine options, repository (in-memory SQLite), service rules, scheduler |
| API          | `tests/api/`             | Every endpoint and status code via `TestClient`, with `get_session` / `get_clock` overridden |
| Regression   | `tests/regression/`      | The sample table from the spec, replayed through the API with a controlled clock (`@pytest.mark.regression`) |
| UI / demo    | `tests/api/test_ui.py`, `test_demo_mode.py`, `test_demo_scenarios.py` | `/ui` and static serving, demo endpoints on and off, every scenario preset, plus guards that the page loads nothing external and never uses `innerHTML` |
| Browser (opt-in) | `tests/e2e/`         | Playwright in Chrome: scenario 2, the guided demo, invalid JSON, and hidden demo controls (`@pytest.mark.e2e`, skipped unless `make e2e`) |

The regression scenarios:

- **A: real-world rejection.** With the real date, all four sample rows return 400 `MATURITY_IN_PAST` and the store stays empty.
- **B: time-ordered replay.** T2 v1 on 2015-03-14, T2 v2 on 2015-03-15, a rejected T2 v1 (409), a same-version replace of T2 v2 into B2, T1 v1 accepted, T3 rejected (already matured in 2015).
- **C: automatic expiry.** T3 stored on 2014-05-01, not expired on its maturity day, expired on 2014-05-21, and the only result for `?expired=true`.

Every rule is covered at all three levels: service unit tests, API tests, and regression scenarios.

```bash
make test         # unit + api, coverage gate 90% (configured in pyproject.toml)
make regression
make install-e2e && make e2e   # optional: browser tests with the locally installed Chrome
```

The browser tests aren't part of CI, because they need a browser. Set
`E2E_BROWSER_CHANNEL=chromium` to use Playwright's bundled Chromium
(after `playwright install chromium`) instead of Chrome.

Current coverage is **100%** (line and branch), and `app/service.py` is at 100%.

## CI/CD pipeline

`.github/workflows/ci.yml` runs on pushes to `main`, on every pull request, and on manual
dispatch. It uses Python 3.12 with pip caching, and every action is pinned to a commit SHA.

```
lint ──► unit-tests ──► regression-tests ──┐
  │                                         ├──► docker
  └────► security-scan ─────────────────────┘
```

| Job                | Steps |
|--------------------|-------|
| `lint`             | `ruff check`, `ruff format --check`, `mypy app` |
| `unit-tests`       | `pytest -m "not regression" --cov=app --cov-fail-under=90`, uploads `coverage.xml` and `reports/unit.xml` |
| `regression-tests` | `pytest -m regression`, uploads `reports/regression.xml` |
| `security-scan`    | Trivy fs scan (fails on CRITICAL), Trivy SARIF uploaded to the Security tab, `pip-audit` (informational) |
| `docker`           | Build image, smoke-test `/health`, Trivy image scan (fails on CRITICAL) |

## Vulnerability scanning policy

- **Gate: any CRITICAL vulnerability fails the build.** Trivy scans the repository
  filesystem (including `requirements*.txt`) with `severity: CRITICAL`, `exit-code: 1`,
  `ignore-unfixed: false`. A second Trivy scan of the built Docker image (OS packages plus
  the installed Python packages) applies the same gate.
- **Visibility: every severity is reported.** A second Trivy run produces SARIF with
  `exit-code: 0` and uploads it to **Security → Code scanning**.
- **Secondary audit.** `pip-audit -r requirements.txt` runs as an informational step. It
  cannot filter by severity, so it does not gate the build.

### Verifying that a CRITICAL CVE fails the build

1. On a branch, add a dependency with a known critical CVE to `requirements.txt`, for example
   `PyYAML==5.3.1` (CVE-2020-14343, arbitrary code execution, CVSS 9.8).
2. Locally: `trivy fs --severity CRITICAL --exit-code 1 .` lists the CVE and exits with code 1
   (`echo $?` prints `1`).
3. Push and open a PR. The **Trivy filesystem scan (fail on CRITICAL)** step fails, so
   `security-scan` fails and `docker` never runs. The finding also appears in the Security tab.
4. Revert the change and the pipeline goes green again.

### Optional: SonarCloud quality gate (fail on BLOCKER)

The workflow has a commented-out SonarCloud step. To enable it:

1. Import the repository at <https://sonarcloud.io> and note the organization and project key.
2. Create a quality gate with the condition **"Blocker issues is greater than 0"** (on overall
   code) and set it as the project's gate.
3. Add a `SONAR_TOKEN` repository secret (Settings → Secrets and variables → Actions).
4. Uncomment the step in `security-scan`, replace `<your-org>`, and optionally add
   `-Dsonar.python.coverage.reportPaths=coverage.xml` (requires moving the step to a job
   that has the coverage report).
5. `-Dsonar.qualitygate.wait=true` makes the step fail when the gate fails, so any BLOCKER
   issue fails the build.

## Design diagrams

PlantUML sources are in [`docs/`](docs). Rendered SVG/PNG files are committed next to
them; regenerate with `make diagrams`.

| Diagram | Source | Rendered |
|---------|--------|----------|
| Class diagram | [`class-diagram.puml`](docs/class-diagram.puml) | [SVG](docs/class-diagram.svg) |
| `POST /trades` sequence | [`sequence-create-trade.puml`](docs/sequence-create-trade.puml) | [SVG](docs/sequence-create-trade.svg) |
| Expiry sweep sequence | [`sequence-expiry-sweep.puml`](docs/sequence-expiry-sweep.puml) | [SVG](docs/sequence-expiry-sweep.svg) |
| Components and CI pipeline | [`component-diagram.puml`](docs/component-diagram.puml) | [SVG](docs/component-diagram.svg) |
| UI submit and clock override sequence | [`sequence-ui-submit.puml`](docs/sequence-ui-submit.puml) | [SVG](docs/sequence-ui-submit.svg) |

![Class diagram](docs/class-diagram.svg)
![Create trade sequence](docs/sequence-create-trade.svg)
![Expiry sweep sequence](docs/sequence-expiry-sweep.svg)
![Component diagram](docs/component-diagram.svg)
![UI submit sequence](docs/sequence-ui-submit.svg)
