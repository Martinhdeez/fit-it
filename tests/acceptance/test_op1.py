"""Acceptance checks for OP1 (test gate and Compose stack).

REQ-OP1-1..3 are checked through the shell and Docker on the host. They cannot run
meaningfully inside the test container (no Docker there, and `make check` would call
itself), so they are a manual checklist; the stubs below only keep each id visible.

Manual checklist, run from the repo root on the host:

REQ-OP1-1  `make check` runs every test under tests/ inside Docker, non-zero on failure.
  1. `make check; echo $?`                -> output shows tests from tests/acceptance/
                                             and tests/unit/ collected; exit code as the
                                             suite dictates.
  2. `printf 'def test_fail():\\n    assert False\\n' > tests/unit/test_zz_fail.py`
     `make check; echo $?`                -> test_zz_fail collected and failed, exit != 0.
  3. `rm tests/unit/test_zz_fail.py`

REQ-OP1-2  `docker compose up` serves the core on localhost:8000, demo has no host port.
  1. `docker compose up -d --build`
  2. `curl -s -o /dev/null -w '%{http_code}\\n' http://localhost:8000/`  -> an HTTP status
     (not a connection error).
  3. `docker compose port demo 8001`      -> no mapping (error or empty).
  4. `curl -s --max-time 3 http://localhost:8001/; echo $?` -> connection refused (exit != 0).
  5. `docker compose down`

REQ-OP1-3  `make check` passes with ANTHROPIC_API_KEY unset.
  1. `env -u ANTHROPIC_API_KEY make check; echo $?`  -> exit 0 (once all items are green).
"""

import pytest


def test_REQ_OP1_1_make_check_runs_all_tests_and_fails_on_failure():
    pytest.skip("manual: shell/Docker on host, see module docstring")


def test_REQ_OP1_2_compose_serves_core_on_8000_and_demo_unpublished():
    pytest.skip("manual: shell/Docker on host, see module docstring")


def test_REQ_OP1_3_make_check_passes_without_api_key():
    pytest.skip("manual: shell/Docker on host, see module docstring")
