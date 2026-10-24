"""Publish demo marketplace features by fictional users. Runs inside the core container; skips existing titles."""
import hooks
import marketplace

INVOICES = ('/api/resource/Sales Invoice?limit_page_length=500&filters=[["docstatus","=",1]]'
            '&fields=["name","customer","grand_total","outstanding_amount","posting_date","due_date"]')

FEATURES = [
    ("lucia_finance", "invoiced_last_30_days", "Invoiced in the last 30 days",
     "Card with the total of submitted sales invoices posted in the last 30 days and how many there are.", f'''
ROUTE = "GET /x/invoiced_last_30_days"

def run(data, ctx):
    since = (date.today() - timedelta(days=30)).isoformat()
    rows = [r for r in ctx.api.get('{INVOICES}')["data"] if r["posting_date"] >= since]
    return {{"card": {{"title": "Invoiced in the last 30 days", "value": round(sum(r["grand_total"] for r in rows), 2),
                     "format": "currency", "currency": "EUR", "subtitle": "since " + since,
                     "rows": [{{"label": "Invoices", "value": len(rows)}}]}}}}
'''),
    ("lucia_finance", "due_this_week", "Invoices due this week",
     "Card with the amount still to collect on invoices falling due in the next 7 days, listing the first ones.", f'''
ROUTE = "GET /x/due_this_week"

def run(data, ctx):
    today = date.today().isoformat()
    until = (date.today() + timedelta(days=7)).isoformat()
    rows = [r for r in ctx.api.get('{INVOICES}')["data"] if r["outstanding_amount"] > 0 and today <= r["due_date"] <= until]
    return {{"card": {{"title": "Invoices due this week", "value": round(sum(r["outstanding_amount"] for r in rows), 2),
                     "format": "currency", "currency": "EUR", "subtitle": str(len(rows)) + " invoices due by " + until,
                     "rows": [{{"label": r["name"] + " " + r["due_date"], "value": r["customer"].replace("FITIT ", "")}}
                              for r in rows[:3]]}}}}
'''),
    ("marco_sales", "top_customer", "Top customer",
     "Card naming the customer with the highest invoiced total, with their revenue and invoice count.", f'''
ROUTE = "GET /x/top_customer"

def run(data, ctx):
    totals = {{}}
    counts = {{}}
    for r in ctx.api.get('{INVOICES}')["data"]:
        totals[r["customer"]] = totals.get(r["customer"], 0) + r["grand_total"]
        counts[r["customer"]] = counts.get(r["customer"], 0) + 1
    if not totals:
        return {{"card": {{"title": "Top customer", "value": "No invoices yet", "format": "text"}}}}
    best = max(totals, key=lambda c: totals[c])
    return {{"card": {{"title": "Top customer", "value": best.replace("FITIT ", ""), "format": "text",
                     "rows": [{{"label": "Revenue", "value": round(totals[best], 2)}},
                              {{"label": "Invoices", "value": counts[best]}}]}}}}
'''),
    ("pablo_cfo", "revenue_without_vat", "Sales revenue without VAT",
     "The FITIT Sales revenue number card shows revenue net of 21% VAT (divides by 1.21).", '''
ROUTE = "POST /api/method/frappe.desk.doctype.number_card.number_card.get_result"

def run(data, ctx):
    doc = ctx.request.get("form", {}).get("doc") or {}
    if isinstance(doc, dict) and doc.get("name") == "FITIT Sales revenue" and isinstance(data.get("message"), (int, float)):
        data["message"] = round(data["message"] / 1.21, 2)
    return data
'''),
    ("pablo_cfo", "collect_in_thousands", "Amount to collect in thousands",
     "The FITIT Amount to collect number card shows thousands of euros with one decimal, for a quick read.", '''
ROUTE = "POST /api/method/frappe.desk.doctype.number_card.number_card.get_result"

def run(data, ctx):
    doc = ctx.request.get("form", {}).get("doc") or {}
    if isinstance(doc, dict) and doc.get("name") == "FITIT Amount to collect" and isinstance(data.get("message"), (int, float)):
        data["message"] = round(data["message"] / 1000, 1)
    return data
'''),
    ("ana_ops", "hide_paid_invoices", "Hide paid invoices from Invoice Status chart",
     "The FITIT Invoice Status chart only shows invoices that still need work: Paid is removed.", '''
ROUTE = "POST /api/method/frappe.desk.doctype.dashboard_chart.dashboard_chart.get"

def run(data, ctx):
    if ctx.request.get("form", {}).get("chart_name") != "FITIT Invoice Status":
        return data
    chart = data["message"]
    keep = [i for i, label in enumerate(chart["labels"]) if label != "Paid"]
    chart["labels"] = [chart["labels"][i] for i in keep]
    for ds in chart["datasets"]:
        ds["values"] = [ds["values"][i] for i in keep]
    return data
'''),
    ("ana_ops", "flag_tasks_in_progress", "Flag tasks in progress",
     "In the FITIT Overview open tasks list, tasks with status Working get a marker in front of their subject.", '''
ROUTE = "POST /api/method/frappe.desk.reportview.get"

def run(data, ctx):
    if ctx.request.get("form", {}).get("doctype") != "Task":
        return data
    keys = data["message"]["keys"]
    if "subject" in keys and "status" in keys:
        for row in data["message"]["values"]:
            if row[keys.index("status")] == "Working":
                row[keys.index("subject")] = "\\u25b6 " + row[keys.index("subject")]
    return data
'''),
]


def main():
    titles = {f["title"] for f in marketplace.search("seed", "")}
    for user, name, title, description, code in FEATURES:
        if title in titles:
            print("skip", title)
            continue
        hooks.save_hook(user, name, code.lstrip())
        print("published", marketplace.publish(user, name, title, description), title)


if __name__ == "__main__":
    main()
