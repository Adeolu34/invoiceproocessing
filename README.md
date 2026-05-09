# Invoice Processing Automation System

A production-grade RPA pipeline that automates the full invoice workflow:

**Email / folder → PDF OCR → Field extraction → Duplicate detection → Browser portal entry → Slack notification**

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    FastAPI (port 8001)                   │
│  POST /invoices/process   GET /invoices   GET /logs      │
└────────────────────────┬────────────────────────────────┘
                         │ dispatch
┌────────────────────────▼────────────────────────────────┐
│                Celery Worker (3 queues)                  │
│  high: process_invoice   default: scan_inbox             │
│  low:  send_notification                                 │
└──────┬──────────────────┬───────────────────────────────┘
       │                  │
┌──────▼──────┐   ┌───────▼───────┐
│  OCR Stage  │   │ Browser Stage │
│ pdfplumber  │   │  Playwright   │
│  Tesseract  │   │  (Chromium)   │
└──────┬──────┘   └───────┬───────┘
       │                  │
┌──────▼──────────────────▼───────┐
│         PostgreSQL               │
│  invoices  |  processing_logs   │
└─────────────────────────────────┘
       │
┌──────▼──────────┐
│  Redis (Celery  │
│  broker/backend)│
└─────────────────┘
```

## Deploy to Render

### 1. Push to GitHub (already done)

### 2. Create a Render Blueprint
- Go to **dashboard.render.com** → **New** → **Blueprint**
- Connect your GitHub repo (`Adeolu34/invoiceproocessing`)
- Render reads `render.yaml` and creates all 5 services automatically:
  - `invoice-web` — FastAPI API
  - `invoice-worker` — Celery worker
  - `invoice-beat` — Celery beat scheduler
  - `invoice-redis` — Redis instance
  - `invoice-postgres` — PostgreSQL database

### 3. Set sensitive environment variables
In the Render dashboard, go to each service → **Environment** and add:

```
SLACK_WEBHOOK_URL=https://hooks.slack.com/...
ACCOUNTING_PORTAL_URL=https://your-portal.com
ACCOUNTING_USER=admin@company.com
ACCOUNTING_PASS=your_password
IMAP_HOST=imap.gmail.com
IMAP_USER=your@gmail.com
IMAP_PASS=your_app_password
SMTP_HOST=smtp.gmail.com
SMTP_USER=your@gmail.com
SMTP_PASS=your_app_password
```

### 4. Deploy
Click **Apply** — Render builds and deploys all services from the same Dockerfile.
The `SERVICE_TYPE` env var controls what each container runs.

Your live API: `https://invoice-web.onrender.com/docs`

> **Note on plan:** Playwright + Chromium needs ~512MB RAM.
> Use `starter` plan ($7/mo) for the web and worker services.
> PostgreSQL and Redis are on the free tier.

---

## Local Development

### 1. Clone and configure

```bash
git clone https://github.com/Adeolu34/invoiceproocessing.git
cd invoiceproocessing
cp .env.example .env
# Edit .env with your credentials
```

### 2. Start all services

```bash
docker compose up --build
```

Services started:
| Service | URL |
|---------|-----|
| FastAPI | http://localhost:8001/docs |
| Flower (Celery monitor) | http://localhost:5555 |
| PostgreSQL | localhost:5432 |
| Redis | localhost:6379 |

### 3. Run database migrations

```bash
docker compose exec app alembic upgrade head
```

### 4. Generate sample invoices (for testing)

```bash
pip install reportlab
python scripts/generate_sample_invoice.py --count 5 --out ./sample_invoices
```

### 5. Process an invoice via API

```bash
curl -X POST http://localhost:8001/invoices/process \
  -F "file=@sample_invoices/sample_invoice_001.pdf"
```

Watch it move through the pipeline:

```bash
# Poll status
curl http://localhost:8001/invoices/1

# View pipeline logs
curl http://localhost:8001/logs?invoice_id=1

# View stats
curl http://localhost:8001/invoices/stats
```

## Running Tests

```bash
pip install -r requirements.txt
pip install aiosqlite  # for in-memory SQLite in tests
pytest
```

Expected output: **38 tests, all passing**

## Pipeline Stages

Each invoice moves through 5 stages tracked in `processing_logs`:

| Stage | What happens |
|-------|-------------|
| `ocr` | pdfplumber extracts text layer; falls back to Tesseract for scanned PDFs |
| `parse` | Regex extracts vendor, invoice number, amount, currency, dates with confidence scores |
| `duplicate_check` | Invoice number checked against DB; duplicates flagged and skipped |
| `portal_entry` | Playwright logs into accounting portal, fills form, submits, captures confirmation |
| `notification` | Slack webhook fires with success/failure/duplicate message |

## Error Handling & Resilience

- **Retry decorator** — exponential backoff on every Playwright step (configurable attempts + delay)
- **Screenshot on failure** — saved to `screenshots/invoice_{id}_failure_{ts}.png`
- **Celery retry queue** — failed invoices re-dispatched every 15 minutes (up to 3 attempts)
- **Manual retry** — `POST /invoices/{id}/retry` forces immediate re-processing
- **Duplicate detection** — prevents double-entry of the same invoice number
- **Structured logging** — JSON logs to file + coloured console via Loguru

## Environment Variables

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection string |
| `REDIS_URL` | Redis connection string |
| `IMAP_HOST` / `IMAP_USER` / `IMAP_PASS` | Email inbox to watch for invoice attachments |
| `SMTP_HOST` / `SMTP_USER` / `SMTP_PASS` | SMTP for confirmation emails |
| `SLACK_WEBHOOK_URL` | Slack incoming webhook for notifications |
| `ACCOUNTING_PORTAL_URL` | URL of the accounting web portal |
| `ACCOUNTING_USER` / `ACCOUNTING_PASS` | Portal credentials |
| `INVOICE_WATCH_FOLDER` | Local folder path to scan for PDF files |
| `SCREENSHOTS_DIR` | Where failure screenshots are saved |

## Project Structure

```
01_invoice_processing/
├── app/
│   ├── automation/
│   │   └── invoice_processor.py   # Playwright browser automation
│   ├── services/
│   │   ├── ocr_service.py         # PDF extraction + field parsing
│   │   ├── email_service.py       # IMAP inbox watcher + SMTP sender
│   │   └── notification_service.py # Slack webhook notifications
│   ├── tasks/
│   │   ├── celery_app.py          # Celery config + beat schedule
│   │   └── invoice_tasks.py       # Pipeline orchestration tasks
│   ├── utils/
│   │   ├── retry.py               # Exponential backoff decorators
│   │   └── logger.py              # Loguru structured logging
│   ├── config.py                  # Pydantic settings
│   ├── database.py                # SQLAlchemy async engine
│   ├── models.py                  # Invoice + ProcessingLog ORM models
│   └── main.py                    # FastAPI app + routes
├── alembic/                       # Database migrations
├── tests/                         # pytest test suite
├── scripts/
│   └── generate_sample_invoice.py # Demo PDF generator
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| API | FastAPI + Uvicorn |
| Task Queue | Celery + Redis |
| Browser Automation | Playwright (Chromium, headless) |
| OCR | pdfplumber + Tesseract + pdf2image |
| Database | PostgreSQL + SQLAlchemy (async) |
| Migrations | Alembic |
| Logging | Loguru |
| Containerisation | Docker + Docker Compose |
