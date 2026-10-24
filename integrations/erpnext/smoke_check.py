"""Check a seeded sandbox through HTTP with a real cookie-backed ERPNext session.

Runs inside Docker, using only the standard library. No API token is required.
"""
import http.cookiejar
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request


class Client:
    def __init__(self, base):
        self.base = base.rstrip("/")
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def request(self, path, payload=None):
        body = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(self.base + path, data=body)
        if body is not None:
            request.add_header("Content-Type", "application/json")
            request.add_header("Origin", self.base)
        with self.opener.open(request, timeout=20) as response:
            return response.read()

    def json(self, path, payload=None):
        return json.loads(self.request(path, payload))

    def method(self, method, **arguments):
        return self.json("/api/method/" + method + "?" + urllib.parse.urlencode(arguments))["message"]


def require_identity(result):
    """Reject guest, malformed or unexpected identity responses."""
    if result.get("message") != "Administrator":
        raise AssertionError("Authenticated ERPNext session was not established")


def verify_http(base, password):
    if not password:
        raise ValueError("ERPNEXT_ADMIN_PASSWORD must be set in the host shell")
    client = Client(base)
    deadline = time.monotonic() + 90
    while True:
        try:
            assert client.json("/api/method/ping")["message"] == "pong"
            break
        except (OSError, AssertionError, ValueError):
            if time.monotonic() >= deadline:
                raise RuntimeError("ERPNext HTTP endpoint did not become ready") from None
            time.sleep(1)
    client.json("/api/method/login", {"usr": "Administrator", "pwd": password})
    require_identity(client.json("/api/method/frappe.auth.get_logged_user"))
    expected = {"Customer": 12, "Supplier": 6, "Item": 18,
                "Sales Order": 20, "Sales Invoice": 12, "Purchase Invoice": 6}
    for doctype, count in expected.items():
        query = urllib.parse.urlencode({"filters": json.dumps([["name", "like", "FITIT%"]]),
                                        "limit_page_length": 100})
        rows = client.json("/api/resource/" + urllib.parse.quote(doctype) + "?" + query)["data"]
        assert len(rows) == count, (doctype, len(rows), count)
    workspace = client.json("/api/resource/Workspace/FITIT%20Overview")["data"]
    assert len(workspace["number_cards"]) == 8
    for link in workspace["number_cards"]:
        card = client.json("/api/resource/Number%20Card/" + urllib.parse.quote(link["number_card_name"]))["data"]
        value = client.method("frappe.desk.doctype.number_card.number_card.get_result",
                              doc=json.dumps(card), filters=card["filters_json"])
        assert value > 0, (card["name"], value)
    for link in workspace["charts"]:
        chart = client.method("frappe.desk.doctype.dashboard_chart.dashboard_chart.get",
                               chart_name=link["chart_name"], refresh=1)
        assert chart["labels"] and any(sum(row["values"]) > 0 for row in chart["datasets"])
    page = client.request("/desk/selling/fitit-overview")
    assert b"frappe" in page and b"<html" in page.lower()
    print("HTTP verified: authenticated session, mock records, 8 cards, 3 charts and desk page", flush=True)
    return {"records": expected, "number_cards": 8, "charts": 3}


if __name__ == "__main__":
    verify_http(os.environ.get("ERPNEXT_CHECK_URL", "http://frontend:8080"),
                os.environ.get("ERPNEXT_ADMIN_PASSWORD", ""))
