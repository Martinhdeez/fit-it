#!/usr/bin/env bash
# Executed inside the bootstrap container; does not rebuild dashboard definitions.
set -euo pipefail
/home/frappe/frappe-bench/env/bin/python /bootstrap/seed_mock_data.py --check
/home/frappe/frappe-bench/env/bin/python /bootstrap/seed_dashboard.py --check
/home/frappe/frappe-bench/env/bin/python /bootstrap/smoke_check.py
