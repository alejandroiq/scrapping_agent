# ARIAXONE Oil Intelligence Agent

This project builds a static oil-market dashboard with WTI and Brent futures price analytics, market commentary, energy news bullets, and X analyst commentary.

## Main Data Source

The dashboard price charts and price snapshot use Yahoo Finance daily futures close prices through `yfinance`:

- WTI futures: `CL=F`
- Brent futures: `BZ=F`

The data pipeline downloads more than 30 calendar days of recent data, then the dashboard displays the latest 10 trading days.

## Prompt And Config Folder

The `/prompts/` folder stores prompt-engineering and dashboard configuration history outside the Python code. Each file keeps the latest active version at the top and preserves older versions below it for rollback.

Every version uses this header:

```text
# ============================================================
# VERSION: YYYY-MM-DD
# STATUS: ACTIVE
# NOTES:
# Short explanation of what changed in this version.
# ============================================================
```

Prompt/config files:

- `prompts/market_commentary.txt`: WTI commentary, Brent commentary, spread interpretation, bullish/bearish factors, and trading implications.
- `prompts/x_commentary.txt`: X/Twitter analyst commentary, institutional rewrites, noisy-post filtering, market implications, and X API notes.
- `prompts/energy_watch.txt`: EIA, IEA, Energy Intelligence, energy news ranking, bullet generation, and article summarization logic.
- `prompts/dashboard_summary.txt`: Executive summary, dashboard overview, daily market wrap-up, and high-level institutional narrative.
- `prompts/dashboard_style.txt`: Fonts, colors, cards, KPI boxes, headings, footer, logo placement, responsive behavior, PDF/print guidance, and ARIAXONE branding notes.
- `prompts/dashboard_data_config.txt`: Charts, WTI/Brent price logic, snapshot cards, latest metrics, last 10 business days tables, APIs, data sources, chart titles, data-source notes, and fallback logic.

## Python Integration

`prompt_loader.py` centralizes prompt/config paths and includes a `load_active_prompt()` helper. The current dashboard behavior is unchanged; existing Python modules still use their current hardcoded prompts and settings, but comments now identify which `/prompts/` file each module should load from in a future dynamic-loading refactor.

Associated modules:

- `agent_report.py`: `market_commentary.txt`, `x_commentary.txt`, and `dashboard_summary.txt`.
- `scraper.py`: `energy_watch.txt` and `x_commentary.txt`.
- `dashboard.py`: `dashboard_style.txt`, `dashboard_data_config.txt`, and `dashboard_summary.txt`.
- `data_pipeline.py`: `dashboard_data_config.txt`.

## Daily Workflow

The daily workflow is split into independent commands:

```powershell
python run_daily_agent.py
```

Generates or skips the local report based on the latest completed CME/Yahoo
trading date. It does not upload or send email.

```powershell
python upload_to_hostinger.py
```

Uploads the five existing report files from `daily_output/`. It does not
generate a report or send email.

```powershell
python send_daily_email.py
```

Reads active recipients from `distribution_list.csv` and sends the published
dashboard link. It does not generate or upload report files.

```powershell
python run_daily_publish.py
```

Runs the complete workflow. It generates when needed, uploads when
`last_report_date.txt` is newer than `last_published_date.txt`, and sends email
when `last_published_date.txt` is newer than `last_emailed_date.txt`.

When used by the scheduled task, `run_daily_publish.py` retries the full
generate/upload/email workflow if data, DNS/network, upload, email, or another
step fails. The retry times are 7:10 PM, 7:25 PM, and 7:45 PM. If all attempts
fail, the failure is logged and the task tries again on the next scheduled day.

The three state files have separate meanings:

- `last_report_date.txt`: latest report successfully generated locally.
- `last_published_date.txt`: latest report successfully uploaded to Hostinger.
- `last_emailed_date.txt`: latest published report successfully emailed.

If email fails after upload, the report remains published and only the email is
retried on the next full workflow run.

## Optional Module Configuration

The daily workflow uses unchanged defaults. Future report agents can request
different windows explicitly without rewriting the working daily modules:

```python
oil_prices = build_market_data(lookback_days=90)

headlines = scrape_all_sources(
    recent_hours=168,
    max_bullets=8,
)

create_dashboard(
    oil_prices,
    market_report,
    output_path="weekly_output/oil_market_dashboard_weekly.html",
    chart_window=30,
    energy_max_bullets=8,
)
```

Calling `build_market_data()`, `scrape_all_sources()`, and
`create_dashboard(...)` without these optional arguments preserves the current
daily settings.

## Email Configuration

Store SMTP credentials outside the source code in `keys.env`,
`env_ariaxone.txt`, or real environment variables:

```env
SMTP_HOST=smtp.example.com
SMTP_PORT=465
SMTP_USER=daily_report@ariaxone.com
SMTP_PASSWORD=YOUR_PRIVATE_PASSWORD
EMAIL_FROM=daily_report@ariaxone.com
```

Recipients are managed in `distribution_list.csv`:

```csv
email,status,notes
recipient@example.com,active,external
old@example.com,inactive,remove later
```

Only `active` rows are used. Inactive rows, duplicates, blank lines, and invalid
email addresses are skipped safely.

Test the email agent without running generation or upload:

```powershell
python send_daily_email.py --test
```

Test messages still use `distribution_list.csv` and include `TEST -` in the
subject. Email results are recorded in `daily_output/email_log.txt`.
