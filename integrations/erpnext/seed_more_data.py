"""Add 90 days of extra sales history up to today for Fit-it charts and cards.

Run in the backend container after seed_mock_data.py. Records are named FITMORE-*;
a day that already has extra records is skipped, so re-running only fills new days
up to today. Other records are never modified or deleted.
"""

import random
from datetime import date, timedelta

import frappe
from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

from seed_mock_data import COMPANY, CUSTOMERS, PRODUCTS

TODAY = date.today()
DAYS = 90
# Uneven customer weights so top-customer charts have a clear ranking.
WEIGHTS = [9, 3, 2, 7, 1, 5, 4, 2, 1, 6, 2, 3]


def create(doctype, key, submit=True, **fields):
    doc = frappe.get_doc({"doctype": doctype, "company": COMPANY, "currency": "EUR",
                         "selling_price_list": "Standard Selling", **fields})
    doc.insert(set_name=key)
    if submit:
        doc.submit()
    return doc


def items(warehouse, trend):
    rows = []
    for p in rng.sample(range(len(PRODUCTS)), rng.randint(1, 3)):
        qty = max(1, round(rng.randint(1, 6) * trend))
        rows.append({"item_code": f"FITIT-ITEM-{p + 1:03d}", "qty": qty,
                     "rate": PRODUCTS[p][1], "warehouse": warehouse})
    return rows


def pay(invoice, key, when):
    if frappe.db.exists("Payment Entry", key):
        return
    payment = get_payment_entry("Sales Invoice", invoice, bank_account=CASH)
    payment.posting_date = payment.reference_date = when
    payment.reference_no = key
    payment.insert(set_name=key)
    payment.submit()
    COUNTS["Payment Entry"] += 1


def seeded(doctype, field, day):
    return frappe.db.exists(doctype, {"name": ["like", "FITMORE-%"], field: day.isoformat()})


def seed(warehouse):
    global rng
    for offset in range(DAYS, -1, -1):
        day = TODAY - timedelta(days=offset)
        rng = random.Random(day.toordinal())  # same data for a given date on every run
        tag = day.strftime("%y%m%d")
        trend = 1 + (DAYS - offset) / DAYS  # sales grow towards today
        per_day = 5 if offset == 0 else rng.choice([1, 1, 2, 2, 3] if offset < 7 else [0, 1, 1, 2, 2, 3])
        for k in range(per_day if not seeded("Sales Invoice", "posting_date", day) else 0):
            customer = "FITIT " + rng.choices(CUSTOMERS, WEIGHTS)[0]
            due = day + timedelta(days=rng.choice([7, 14, 30]))
            key = f"FITMORE-SI-{tag}-{k + 1}"
            create("Sales Invoice", key, customer=customer, posting_date=day.isoformat(),
                   posting_time=f"{9 + k}:00:00", set_posting_time=1,
                   due_date=due.isoformat(), update_stock=0,
                   remarks="FITIT extra demo sales invoice", items=items(warehouse, trend))
            COUNTS["Sales Invoice"] += 1
            # About 60% paid; today's first three paid; the rest stay unpaid (overdue if past due).
            if (offset == 0 and k < 3) or (offset > 0 and rng.random() < 0.6):
                paid_on = min(TODAY, day + timedelta(days=rng.randint(0, 20)))
                pay(key, f"FITMORE-PAY-{tag}-{k + 1}", paid_on.isoformat())
        if offset % 2 == 0 and not seeded("Sales Order", "transaction_date", day):
            customer = rng.choices(CUSTOMERS, WEIGHTS)[0]
            # Customer in the name so name-only list views can still group orders.
            doc = create("Sales Order", f"FITMORE-SO-{tag} {customer}", submit=offset % 7 != 3,
                         customer=f"FITIT {customer}", transaction_date=day.isoformat(),
                         delivery_date=(day + timedelta(days=10)).isoformat(),
                         items=items(warehouse, trend))
            COUNTS["Sales Order"] += 1
            if doc.docstatus == 1 and offset % 5 == 0:
                doc.update_status("Closed")
        if offset % 9 == 0 and not seeded("Quotation", "transaction_date", day):
            create("Quotation", f"FITMORE-QUO-{tag}", submit=offset % 27 != 0,
                   quotation_to="Customer", party_name="FITIT " + rng.choices(CUSTOMERS, WEIGHTS)[0],
                   transaction_date=day.isoformat(),
                   valid_till=(day + timedelta(days=30)).isoformat(),
                   items=items(warehouse, trend))
            COUNTS["Quotation"] += 1
        frappe.db.commit()


def main():
    global CASH
    frappe.init(site="frontend", sites_path="/home/frappe/frappe-bench/sites")
    frappe.connect()
    frappe.set_user("Administrator")
    frappe.flags.mute_emails = True
    try:
        assert frappe.db.exists("Company", COMPANY), "Run seed_mock_data.py first"
        CASH = frappe.db.get_value("Account", {"company": COMPANY, "account_type": "Cash", "is_group": 0})
        seed("FITIT Main Warehouse - FDS")
        print("Added:", COUNTS, flush=True)
    except Exception:
        frappe.db.rollback()
        raise
    finally:
        frappe.destroy()


COUNTS = {"Sales Invoice": 0, "Sales Order": 0, "Quotation": 0, "Payment Entry": 0}

if __name__ == "__main__":
    main()
