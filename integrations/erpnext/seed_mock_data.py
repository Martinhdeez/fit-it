"""Populate the local ERPNext sandbox through validated Frappe documents.

Run in the backend container using its Python environment. Re-running preserves
existing FITIT records. --check verifies the dataset without creating documents.
"""

import argparse
import json
import os
from datetime import date, timedelta

import frappe

COMPANY = "FITIT Demo Supplies"
ABBR = "FDS"
TODAY = date.fromisoformat(os.environ.get("FITIT_SEED_DATE", "2026-10-24"))
EXPECTED = {
    "Customer": 12, "Supplier": 6, "Item": 18, "Item Price": 36,
    "Address": 18, "Contact": 18, "Lead": 12, "Quotation": 8,
    "Sales Order": 20, "Sales Invoice": 12, "Purchase Order": 8,
    "Purchase Invoice": 6, "Payment Entry": 6, "Stock Entry": 1,
    "Project": 2, "Task": 10,
}
PRODUCTS = [
    ("Laptop Pro 14", 1190, 820), ("Monitor 27 inch", 285, 175),
    ("USB-C Dock", 125, 72), ("Wireless Keyboard", 59, 30),
    ("Wireless Mouse", 29, 12), ("Office Headset", 79, 42),
    ("HD Webcam", 65, 33), ("Wi-Fi Router", 145, 85),
    ("Network Switch", 99, 54), ("Ethernet Cable 2m", 9, 3),
    ("Laptop Stand", 39, 18), ("External SSD 1TB", 115, 71),
    ("Office Chair", 225, 135), ("Standing Desk", 415, 260),
    ("Desk Lamp", 45, 21), ("Onsite Installation", 85, 45),
    ("Monthly Support", 150, 80), ("Team Training", 350, 190),
]
CUSTOMERS = [
    "Aurora Clinic", "Brisa Architecture", "Cedar Academy", "Delta Logistics",
    "Estrella Design", "Faro Consulting", "Granada Retail", "Horizonte Labs",
    "Isla Coworking", "Juniper Hotel", "Kiwi Media", "Luna Engineering",
]
SUPPLIERS = ["Atlas Hardware", "Boreal Electronics", "Coral Furniture",
             "Duna Networks", "Eclipse Services", "Fenix Accessories"]


def day(offset):
    return (TODAY + timedelta(days=offset)).isoformat()


def ensure(doctype, key, *, submit=False, **fields):
    if frappe.db.exists(doctype, key):
        doc = frappe.get_doc(doctype, key)
    else:
        doc = frappe.get_doc({"doctype": doctype, **fields})
        doc.insert(set_name=key)
        if submit:
            doc.submit()
        print(f"Created {doctype}: {doc.name}", flush=True)
    return doc


def setup():
    from frappe.desk.page.setup_wizard.setup_wizard import setup_complete

    if not frappe.is_setup_complete():
        setup_complete({
            "company_name": COMPANY, "company_abbr": ABBR,
            "country": "Spain", "currency": "EUR", "language": "English",
            "timezone": "Europe/Madrid", "chart_of_accounts": "Standard",
            "fy_start_date": f"{TODAY.year}-01-01",
            "fy_end_date": f"{TODAY.year}-12-31", "enable_telemetry": 0,
        })
        frappe.db.commit()
    if not frappe.db.exists("Company", COMPANY):
        raise RuntimeError("This seed is intended for the FITIT sandbox company only")
    assert frappe.is_setup_complete(), "Setup wizard did not complete"
    frappe.flags.in_setup_wizard = False
    warehouse = ensure("Warehouse", "FITIT Main Warehouse - FDS",
                       warehouse_name="FITIT Main Warehouse", company=COMPANY,
                       parent_warehouse="All Warehouses - FDS", is_group=0)
    return warehouse.name


def masters(warehouse):
    for kind, names in (("Customer", CUSTOMERS), ("Supplier", SUPPLIERS)):
        for i, label in enumerate(names, 1):
            key = f"FITIT {label}"
            fields = ({"customer_name": key, "customer_type": "Company",
                       "customer_group": "Commercial", "territory": "Spain"}
                      if kind == "Customer" else
                      {"supplier_name": key, "supplier_type": "Company",
                       "supplier_group": "All Supplier Groups"})
            ensure(kind, key, **fields)
            email = f"{kind.lower()}{i:02d}@fitit.example.test"
            link = [{"link_doctype": kind, "link_name": key}]
            ensure("Contact", f"FITIT-{kind}-Contact-{i:02d}",
                   first_name="Demo", last_name=label, links=link,
                   email_ids=[{"email_id": email, "is_primary": 1}])
            ensure("Address", f"FITIT-{kind}-Address-{i:02d}",
                   address_title=key, address_type="Billing", country="Spain",
                   city=["Madrid", "Barcelona", "Valencia"][i % 3],
                   address_line1=f"Demo Business Street {i}", pincode="28001",
                   email_id=email, links=link)
    for i, (label, sell, buy) in enumerate(PRODUCTS, 1):
        key = f"FITIT-ITEM-{i:03d}"
        ensure("Item", key, item_code=key, item_name=label,
               item_group="Products" if i <= 15 else "Services", stock_uom="Nos",
               is_stock_item=int(i <= 15), description=f"Fictional Fit-it test item: {label}",
               item_defaults=[{"company": COMPANY, "default_warehouse": warehouse}])
        for kind, price in (("Selling", sell), ("Buying", buy)):
            ensure("Item Price", f"FITIT-PRICE-{kind}-{i:03d}", item_code=key,
                   price_list=f"Standard {kind}", price_list_rate=price, currency="EUR")
    for i in range(1, 13):
        ensure("Lead", f"FITIT-LEAD-{i:03d}", first_name="Demo",
               last_name=f"Prospect {i:02d}", company_name=f"FITIT Prospect Company {i:02d}",
               email_id=f"lead{i:02d}@fitit.example.test",
               status=["Lead", "Open", "Interested"][i % 3])
    frappe.db.commit()


def line_items(index, warehouse, buying=False):
    rows = []
    for n in range(2):
        p = (index * 2 + n) % len(PRODUCTS)
        rows.append({"item_code": f"FITIT-ITEM-{p + 1:03d}",
                     "qty": 1 + index % 4, "rate": PRODUCTS[p][2 if buying else 1],
                     "warehouse": warehouse})
    return rows


def transactions(warehouse):
    ensure("Stock Entry", "FITIT-STOCK-OPENING", submit=True,
           company=COMPANY, stock_entry_type="Material Receipt",
           posting_date=day(-90), posting_time="08:00:00", set_posting_time=1,
           remarks="FITIT mock opening inventory",
           items=[{"item_code": f"FITIT-ITEM-{i:03d}", "qty": 120 + i * 10,
                   "t_warehouse": warehouse, "basic_rate": product[2]}
                  for i, product in enumerate(PRODUCTS[:15], 1)])
    frappe.db.commit()
    for i in range(1, 9):
        ensure("Quotation", f"FITIT-QUO-{i:03d}", submit=i <= 5,
               company=COMPANY, quotation_to="Customer",
               party_name=f"FITIT {CUSTOMERS[(i - 1) % 12]}",
               transaction_date=day(-10 + i), valid_till=day(20 + i),
               selling_price_list="Standard Selling", currency="EUR",
               items=line_items(i, warehouse))
    for i in range(1, 21):
        ensure("Sales Order", f"FITIT-SO-{i:03d}", submit=i <= 15,
               company=COMPANY, customer=f"FITIT {CUSTOMERS[(i - 1) % 12]}",
               transaction_date=day(-30 + i), delivery_date=day(5 + i),
               selling_price_list="Standard Selling", currency="EUR",
               items=line_items(i, warehouse))
    for i in range(1, 13):
        ensure("Sales Invoice", f"FITIT-SI-{i:03d}", submit=i <= 10,
               company=COMPANY, customer=f"FITIT {CUSTOMERS[(i - 1) % 12]}",
               posting_date=day(-40 + i), posting_time="12:00:00", set_posting_time=1,
               due_date=day(-10 + i * 2), update_stock=1,
               selling_price_list="Standard Selling", currency="EUR",
               remarks="FITIT mock sales invoice", items=line_items(i, warehouse))
    for i in range(1, 9):
        ensure("Purchase Order", f"FITIT-PO-{i:03d}", submit=i <= 6,
               company=COMPANY, supplier=f"FITIT {SUPPLIERS[(i - 1) % 6]}",
               transaction_date=day(-15 + i), schedule_date=day(10 + i),
               buying_price_list="Standard Buying", currency="EUR",
               items=line_items(i, warehouse, buying=True))
    for i in range(1, 7):
        ensure("Purchase Invoice", f"FITIT-PI-{i:03d}", submit=i <= 4,
               company=COMPANY, supplier=f"FITIT {SUPPLIERS[(i - 1) % 6]}",
               posting_date=day(-25 + i), posting_time="10:00:00", set_posting_time=1,
               due_date=day(5 + i), bill_no=f"FITIT-VENDOR-{i:03d}",
               bill_date=day(-25 + i), update_stock=0,
               buying_price_list="Standard Buying", currency="EUR",
               remarks="FITIT mock purchase invoice", items=line_items(i, warehouse, buying=True))
    frappe.db.commit()
    from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

    cash = frappe.db.get_value("Account", {"company": COMPANY, "account_type": "Cash", "is_group": 0})
    assert cash, "A cash account is required for mock payments"
    for kind, count, prefix in (("Sales Invoice", 4, "SI"), ("Purchase Invoice", 2, "PI")):
        for i in range(1, count + 1):
            key = f"FITIT-PAY-{prefix}-{i:03d}"
            if frappe.db.exists("Payment Entry", key):
                continue
            payment = get_payment_entry(kind, f"FITIT-{prefix}-{i:03d}", bank_account=cash)
            payment.posting_date = day(-2)
            payment.reference_no = key
            payment.reference_date = day(-2)
            payment.insert(set_name=key)
            payment.submit()
            print(f"Created Payment Entry: {key}", flush=True)
    frappe.db.commit()


def projects():
    for i, label in enumerate(("Office Equipment Rollout", "Customer Portal Pilot"), 1):
        project = ensure("Project", f"FITIT-PROJECT-{i:03d}",
                         project_name=f"FITIT {label}", company=COMPANY,
                         customer=f"FITIT {CUSTOMERS[i - 1]}",
                         expected_start_date=day(-10), expected_end_date=day(30),
                         status="Open")
        for n, task in enumerate(("Gather requirements", "Prepare equipment", "Configure access",
                                  "Run acceptance checks", "Deliver training"), 1):
            ensure("Task", f"FITIT-TASK-{i}-{n:03d}", subject=f"FITIT {task}",
                   project=project.name, status="Working" if n <= 2 else "Open",
                   priority="High" if n == 1 else "Medium",
                   exp_start_date=day(n), exp_end_date=day(n + 7),
                   description="Fictional task for Fit-it integration testing.")
    frappe.db.commit()


def verify():
    counts = {dt: frappe.db.count(dt, {"name": ["like", "FITIT%"]}) for dt in EXPECTED}
    for dt, expected in EXPECTED.items():
        assert counts[dt] == expected, f"{dt}: expected {expected}, found {counts[dt]}"
    assert frappe.is_setup_complete()
    assert frappe.db.count("Sales Invoice", {"name": ["like", "FITIT%"], "docstatus": 1}) == 10
    assert frappe.db.count("Payment Entry", {"name": ["like", "FITIT%"], "docstatus": 1}) == 6
    for i in range(1, 5):
        outstanding = frappe.db.get_value("Sales Invoice", f"FITIT-SI-{i:03d}", "outstanding_amount")
        assert abs(outstanding) < 0.01, f"Sales invoice {i} not fully paid"
    bins = frappe.get_all("Bin", filters={"warehouse": "FITIT Main Warehouse - FDS"},
                         fields=["item_code", "actual_qty"])
    assert len(bins) == 15 and all(row.actual_qty > 0 for row in bins)
    ledger = frappe.get_all("GL Entry", filters={"company": COMPANY, "is_cancelled": 0},
                           fields=["debit", "credit"])
    imbalance = sum(float(row.debit) - float(row.credit) for row in ledger)
    assert ledger and abs(imbalance) < 0.01, f"General ledger imbalance: {imbalance}"
    statuses = frappe.get_all("Sales Invoice", filters={"name": ["like", "FITIT%"]},
                             fields=["name", "status", "grand_total", "outstanding_amount"])
    print(json.dumps({"company": COMPANY, "records": counts, "stock_items": len(bins),
                      "gl_entries": len(ledger), "ledger_balanced": True,
                      "sales_invoices": statuses}, indent=2, default=str), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    frappe.init(site="frontend", sites_path="/home/frappe/frappe-bench/sites")
    frappe.connect()
    frappe.set_user("Administrator")
    frappe.flags.mute_emails = True
    try:
        if not args.check:
            warehouse = setup()
            masters(warehouse)
            transactions(warehouse)
            projects()
        verify()
    except Exception:
        frappe.db.rollback()
        raise
    finally:
        frappe.destroy()


if __name__ == "__main__":
    main()
