# ERPNext local sandbox

Recreate the Fit-it sample ERP on another machine: ERPNext **16.52.1**, the
**FITIT Overview** workspace and a populated fictional company. This folder is
self-contained; it needs Docker, not a local Python installation or an ERPNext clone.
It does not change Fit-it configuration or connect Fit-it to ERPNext.

## Quick start

Requirements: Docker with Linux containers and Compose v2, plus PowerShell 7+ on
Windows or Bash on Linux/macOS. The runtime was verified on linux/amd64; ARM hosts
may need Docker's amd64 emulation if the pinned ERPNext image has no native variant.

From the repository root, in PowerShell:

```powershell
$env:ERPNEXT_DB_PASSWORD = Read-Host 'Choose a local database root password' -MaskInput
$env:ERPNEXT_ADMIN_PASSWORD = Read-Host 'Choose an ERPNext Administrator password' -MaskInput
./integrations/erpnext/bootstrap.ps1
```

Or in Bash:

```bash
read -r -s -p 'Choose a local database root password: ' ERPNEXT_DB_PASSWORD
printf '\n'
read -r -s -p 'Choose an ERPNext Administrator password: ' ERPNEXT_ADMIN_PASSWORD
printf '\n'
export ERPNEXT_DB_PASSWORD ERPNEXT_ADMIN_PASSWORD
bash integrations/erpnext/bootstrap.sh
```

The scripts also work by absolute path, from another directory. First startup
takes several minutes while ERPNext installs its schema. Startup waits for the
database, site installation and backend health before seeding. A failed command
stops the script with a nonzero exit code.

- Open **http://localhost:8080/desk/selling/fitit-overview**.
- User: **Administrator**. Password: the value you chose in your shell.
- Navigation: **Selling → FITIT Overview**.
- The port is bound to localhost. Database and Redis ports are not published.
- Secrets come from the host shell. No credentials or `.env` file are included.

In a new shell, set the **same** credentials before running Compose again.
Changing an environment variable does not reset an existing site's password.

## Included interface and data

Company: `FITIT Demo Supplies`; country: Spain; currency: EUR; timezone:
Europe/Madrid. The fixture date defaults to **2026-10-24**, so records and charts
reproduce the original demonstration rather than shifting whenever they are seeded.

| Dataset | Count |
| --- | ---: |
| Customers / suppliers | 12 / 6 |
| Products and services / prices | 18 / 36 |
| Addresses / contacts / leads | 18 / 18 / 12 |
| Quotations / sales orders / purchase orders | 8 / 20 / 8 |
| Sales invoices / purchase invoices / payments | 12 / 6 / 6 |
| Opening stock entry / projects / tasks | 1 / 2 / 10 |

There are 193 primary fixture records, plus the company, warehouse and generated
accounting/stock records. Fifteen products have positive stock. Documents include
drafts, confirmed orders, paid invoices and unpaid invoices. Amounts are fictional,
without tax configuration; orders and invoices are independent test scenarios.
Emails use the reserved `.test` domain, and seeding mutes outgoing mail.

The workspace contains eight live Number Cards, three customer cards, three
charts, open tasks and four shortcuts. Customer cards open their Customer records.
Native ERPNext permissions apply to data requests. Expected starting indicators:

- Sales revenue: **EUR 8,390**.
- Confirmed sales orders: **15**.
- Active customers: **12**.
- Amount to collect: **EUR 6,750**.

The layout includes the responsive correction from the local demo. Its CSS is
scoped to this workspace. The Nginx template preserves the browser's Origin for
Socket.IO instead of rewriting it to the internal site name; the upstream origin
check remains enabled.

## Configuration and lifecycle

| Host environment variable | Default / meaning |
| --- | --- |
| `ERPNEXT_DB_PASSWORD` | Required; MariaDB root password |
| `ERPNEXT_ADMIN_PASSWORD` | Required; site Administrator password |
| `ERPNEXT_PROJECT_NAME` | `fitit-erpnext`; isolates containers, network and volumes |
| `ERPNEXT_HOST_PORT` | `8080`; localhost HTTP port |
| `FITIT_SEED_DATE` | `2026-10-24`; ISO date, set before first seeding |

For an independent installation alongside an existing one, set a different
`ERPNEXT_PROJECT_NAME` and `ERPNEXT_HOST_PORT` before startup. Named volumes persist
the site's database, files, logs and queue data. Stopping containers preserves them:

```bash
docker compose -f integrations/erpnext/compose.yaml stop
docker compose -f integrations/erpnext/compose.yaml logs --tail 50 create-site backend
```

Do not use `down -v` unless you intend to erase that installation's data.
Initialization refuses an existing site whose ERPNext installation is incomplete.
Inspect its logs before deciding how to recover it.

Re-running bootstrap preserves existing fixture records. Dashboard definitions are
updated to this version. It does not delete records or reset manual transaction edits.
Keep the same seed date on reruns. Verification checks the original fixture counts
and balances, and can intentionally fail after you change those records in tests.

## Verify

Bootstrap checks fixture counts, settled invoices, positive stock and a balanced
general ledger, then queries the cards and charts through a real authenticated HTTP
session. Verify existing records without rebuilding the workspace:

```bash
docker compose -f integrations/erpnext/compose.yaml run --rm -T bootstrap 'bash /bootstrap/verify.sh'
```

This command works in Bash and PowerShell. No model/API keys are needed to start
or check this ERPNext sandbox.

## Integration handoff

This delivery provides the ERP only. Fit-it continues to use its existing demo
configuration. Integration work for the next task:

1. Connect the stacks through a Docker network and configure Fit-it's `BASE_URL`.
2. Adapt user resolution: Fit-it currently expects `/api/me` returning `{"id": ...}`;
   Frappe uses `/api/method/frappe.auth.get_logged_user` returning `{"message": ...}`.
   Preserve each user's own session; do not use a shared Administrator API token.
3. Verify session cookies, CSRF, static assets and Socket.IO through the proxy.
4. Check widget injection and per-user hooks against a normal ERPNext account.

Useful direct ERP endpoints: `/api/method/ping`, `/api/method/login`,
`/api/method/frappe.auth.get_logged_user`, `/api/resource/Customer`,
`/api/resource/Sales%20Invoice` and `/desk/selling/fitit-overview`.

## Run behind Fit-it

After bootstrap, from the repository root: `docker compose -p fitit-demo -f compose.yaml -f integrations/erpnext/compose.fitit.yaml up -d --build core`.
Open **http://localhost:8000/desk/selling/fitit-overview** and log in as Administrator; the widget is injected there.
The overlay sets `BASE_URL=http://frontend:8080`, `FITIT_USER_PATH=/api/method/frappe.auth.get_logged_user`, `FITIT_USER_FIELD=message` and `FITIT_ANONYMOUS_USERS=Guest`.
Limit: Socket.IO realtime is not proxied (no websocket passthrough); the desk works without live updates.

## Share and reproduce

Share this Git branch/PR, or a ZIP containing only this directory. The source
checkouts, Docker volumes and images are not required in Git. The scripts recreate
the published sample; a database backup is needed only to preserve later manual edits.

For an exact private snapshot, Frappe can generate a backup inside the site's volume:

```bash
docker compose -f integrations/erpnext/compose.yaml exec backend bench --site frontend backup --with-files
```

Backups and site configuration can contain credentials; share them privately and
keep them out of Git. This repository intentionally ships reconstruction scripts.

Seed the Fit-it marketplace with demo features (idempotent, skips existing titles):
`docker cp integrations/erpnext/seed_marketplace.py fitit-demo-core-1:/tmp/ && docker exec -e PYTHONPATH=/app fitit-demo-core-1 uv run python /tmp/seed_marketplace.py`

## Provenance

- ERPNext runtime: `frappe/erpnext:v16.52.1`, pinned by digest in Compose.
- MariaDB 11.8 and Redis 6.2 Alpine are also pinned by digest.
- Compose and Nginx template derived from the official
  [frappe_docker](https://github.com/frappe/frappe_docker) setup at `ce862c7`.
  Upstream's MIT license is included as `LICENSE.frappe-docker`.
- Interface uses native [Frappe Workspace blocks](https://docs.frappe.io/framework/user/en/desk/navigation/workspace).
- This is a local evaluation fixture, not a production deployment.
