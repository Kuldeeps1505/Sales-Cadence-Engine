# Sales Cadence Engine 🚀

A production-grade sales outreach automation system built to demonstrate the full **Celery async task orchestration** ecosystem — `chain`, `group`, `chord`, retries, result backend, and worker scaling.

---

## Tech Stack

| Layer | Tech |
|---|---|
| Backend | FastAPI + SQLAlchemy |
| Task Queue | Celery 5 + Redis |
| Database | PostgreSQL 15 |
| Frontend | Next.js 14 + Tailwind CSS |
| Monitoring | Flower |
| Container | Docker Compose |

---

## Workflow Architecture

When **Run Full Workflow** is triggered for a campaign, each lead goes through a 3-stage async pipeline:

```
launch_campaign(campaign_id)
  └── FOR EACH lead → run_lead_workflow(campaign_lead_id)
        │
        ├── STAGE 1 — chord (parallel validation)
        │     group(
        │       validate_lead()      ← phone/email format check
        │       check_dnc()          ← DNC list check
        │       verify_email_addr()  ← email validity check
        │     ) ──► aggregate_validation()  ← fires when ALL 3 done
        │
        ├── STAGE 2 — chain (sequential call pipeline)
        │     simulate_call()        ← attempt call, exponential backoff retry
        │       ──► send_real_email() ← post-call followup OR retry-exhausted email
        │             ──► finalize_lead()  ← write audit log, mark complete
        │
        └── STAGE 3 — chord (parallel reporting)
              group(
                report_calls()
                report_emails()
                report_conversion()
              ) ──► combine_reports()  ← aggregated campaign summary
```

### Celery Primitives Used

| Primitive | Where | Purpose |
|---|---|---|
| `chain()` | Stage 2 | `simulate_call → send_email → finalize` — result passes to next task |
| `group()` | Stage 1 & 3 | 3 validation tasks fire simultaneously |
| `chord()` | Stage 1 & 3 | Parallel tasks → single callback when all complete |
| `retry` | All tasks | Exponential backoff: `delay × 2^(attempt-1)` |
| `signals` | `celery_app.py` | `task_prerun/postrun/failure/retry` → auto-update `TaskRecord` table |

---

## Worker Scaling

5 independent queues — scale each worker type based on load:

```bash
worker_validation   -c 4   # fast parallel checks
worker_calls        -c 4   # CPU-heavy (call simulation)
worker_emails       -c 2   # I/O-heavy (SMTP)
worker_cadence      -c 2   # orchestration (chain/chord launchers)
worker_reporting    -c 2   # analytics aggregation
```

---

## Email Pipeline

Three real HTML emails sent via Gmail SMTP (App Password):

| Trigger | Template |
|---|---|
| Call answered | Post-call follow-up |
| Max retries exhausted | "We tried calling you Nx" |
| Direct outreach | Cold outreach with context |

Set `SMTP_PORT=465` in `.env` for SSL — more reliable inside Docker than STARTTLS.

---

## Result Backend

Every Celery task is tracked in PostgreSQL via `TaskRecord`:

```
GET /api/v1/tasks/{task_id}   → PENDING | STARTED | SUCCESS | FAILURE | RETRY
GET /api/v1/campaigns/{id}/workflow-status  → per-lead live states
GET /api/v1/campaigns/{id}/report           → calls / emails / conversion stats
```

---

## Quick Start

```bash
# 1. Clone and configure
cp backend/.env.example backend/.env
# Fill in DATABASE_URL, REDIS_URL, SMTP_* in .env

# 2. Start everything
docker-compose up --build

# 3. Open
# Frontend  → http://localhost:3000
# API docs  → http://localhost:8000/docs
# Flower    → http://localhost:5555
```

---

## Project Structure

```
sales-cadence/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI routes
│   │   ├── models.py            # SQLAlchemy models + TaskRecord
│   │   ├── workers/
│   │   │   ├── celery_app.py    # Celery config + lifecycle signals
│   │   │   └── tasks.py         # All 13 tasks (chain/group/chord)
│   │   └── services/
│   │       └── email_service.py # SMTP sender + HTML templates
├── frontend/
│   └── my-app/
│       └── app/page.tsx         # Next.js dashboard
└── docker-compose.yml           # 9 services
```

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/leads/upload` | Upload CSV |
| `GET` | `/api/v1/leads/` | List all leads |
| `POST` | `/api/v1/leads/{id}/dnc` | Mark lead as DNC |
| `POST` | `/api/v1/campaigns/` | Create campaign |
| `POST` | `/api/v1/campaigns/{id}/run-workflow` | Trigger full chain+group+chord |
| `GET` | `/api/v1/campaigns/{id}/logs` | Audit log |
| `GET` | `/api/v1/campaigns/{id}/workflow-status` | Live per-lead states |
| `GET` | `/api/v1/campaigns/{id}/report` | Campaign report |
| `GET` | `/api/v1/tasks/{task_id}` | Task result backend poll |

---

## CSV Format

```csv
name,email,phone,company,language,notes
Rahul Sharma,rahul@example.com,9876543210,TechNova,english,Interested in AI tools
```

> **Tip:** Use Gmail `+` aliases (`you+lead1@gmail.com`) to test multiple leads routing to one inbox.
