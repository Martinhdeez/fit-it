"""Create and verify a native ERPNext workspace over the FITIT mock dataset."""
import argparse
import json

import frappe

COMPANY = "FITIT Demo Supplies"
PAGE = "FITIT Overview"


def save(doctype, name, **values):
    doc = (frappe.get_doc(doctype, name) if frappe.db.exists(doctype, name)
           else frappe.new_doc(doctype))
    doc.update(values)
    if doc.is_new():
        doc.insert(set_name=name)
    else:
        doc.save()
    return doc


def filters(doctype, *extra):
    return json.dumps([[doctype, "name", "like", "FITIT%"], *extra])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify without changing dashboard documents")
    args = parser.parse_args()
    frappe.init(site="frontend", sites_path="/home/frappe/frappe-bench/sites")
    frappe.connect()
    frappe.set_user("Administrator")
    try:
        if not args.check:
            build()
        verify()
        frappe.db.commit()
        frappe.clear_cache()
    except Exception:
        frappe.db.rollback()
        raise
    finally:
        frappe.destroy()


def build():
    save("Custom HTML Block", "FITIT Activity Header", private=0, html='''
<section class="overview" aria-label="Company overview">
  <div><div class="eyebrow">FITIT DEMO SUPPLIES / BUSINESS OVERVIEW</div>
  <h2>Your business at a glance.</h2>
  <p>Sales, customers and daily operations in one place.</p></div>
  <div class="context"><span class="badge">SAMPLE DATA</span>
  <time class="date"></time><span>Spain · EUR</span></div>
</section>''', style='''
:host{font-family:inherit;color:#fff}*{box-sizing:border-box}
.overview{background:#44358b;border-radius:12px;padding:26px 30px;display:flex;
justify-content:space-between;align-items:center;gap:22px;min-height:138px}
.eyebrow{font-size:10px;letter-spacing:1.7px;font-weight:650;color:#d8d0ff}
h2{color:#fff;font-size:28px;font-weight:650;line-height:1.2;letter-spacing:-.8px;margin:10px 0}
p{font-size:13px;margin:0;color:#e7e2ff}.context{display:flex;align-items:flex-end;
flex-direction:column;gap:9px;font-size:12px;white-space:nowrap;color:#e7e2ff}
.badge{background:#ffffff20;border:1px solid #ffffff30;padding:5px 9px;
border-radius:5px;font-size:9px;font-weight:650;letter-spacing:1px}
@media(max-width:650px){.overview{padding:20px;align-items:flex-start;flex-direction:column}
h2{font-size:23px}.context{align-items:flex-start;flex-direction:row;flex-wrap:wrap}}
''', script='''root_element.querySelector('.date').textContent = new Intl.DateTimeFormat(
'en-GB', {day:'numeric',month:'long',year:'numeric'}).format(new Date());
// Scope layout rules to this workspace; other ERPNext pages keep their own layout.
let layout = document.getElementById('fitit-overview-layout');
if (!layout) { layout=document.createElement('style'); layout.id='fitit-overview-layout'; document.head.append(layout); }
const scope='body[data-route="Workspaces/FITIT Overview"]';
layout.textContent=`
${scope} .layout-main-section-wrapper, ${scope} .layout-main-section,
${scope} .editor-js-container, ${scope} .codex-editor {min-width:0;max-width:100%;box-sizing:border-box}
${scope} .ce-block, ${scope} .ce-block__content, ${scope} .widget {min-width:0;max-width:100%;box-sizing:border-box}
${scope} .dashboard-widget-box .widget-head {flex-wrap:wrap;gap:8px}
${scope} .dashboard-widget-box .widget-control {display:flex;flex-wrap:wrap;max-width:100%;gap:4px}
${scope} .dashboard-widget-box .chart-container {width:100%;max-width:100%;min-width:0}
@media(max-width:1100px) {
 ${scope} .ce-block.col-xs-8, ${scope} .ce-block:has(.dashboard-widget-box),
 ${scope} .ce-block:has(.quick-list-widget-box) {width:100%!important;flex-basis:100%!important}
}
@media(max-width:700px) {
 ${scope} .ce-block {width:100%!important;flex-basis:100%!important}
 ${scope} .dashboard-widget-box .widget-control {justify-content:flex-start}
}`;''')

    save("Custom HTML Block", "FITIT Featured Customers", private=0, html='''
<section aria-label="Featured customer cards">
<div class="section-title"><h3>Customer spotlight</h3><span>From your sample customer portfolio</span></div>
<div class="customers" aria-live="polite"><p>Loading customer activity…</p></div>
<template id="customer-card"><article class="customer">
  <div class="identity"><div class="avatar" aria-hidden="true"></div><div>
  <button class="customer-name" type="button"></button><p class="customer-type">Business customer · Spain</p></div></div>
  <div class="stats"><div><strong class="orders"></strong><span>Orders</span></div>
  <div><strong class="sales"></strong><span>Invoiced</span></div>
  <div><strong class="balance"></strong><span>Outstanding</span></div></div>
</article></template></section>''', style='''
:host{font-family:inherit;color:var(--text-color,#252438)}*{box-sizing:border-box}
.section-title{display:flex;align-items:baseline;justify-content:space-between;gap:12px;margin:6px 0 14px}
h3{font-size:15px;margin:0;font-weight:650}.section-title>span{font-size:11px;color:var(--text-muted,#687078)}
.customers{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}
.customer{padding:20px;border:1px solid var(--border-color,#e7e6ed);border-radius:10px;background:var(--card-bg,#fff)}
.identity{display:flex;align-items:center;gap:11px}.avatar{flex-shrink:0;width:40px;height:40px;border-radius:50%;
background:#ede8fc;color:#534196;display:grid;place-items:center;font-size:13px;font-weight:700}
.customer:nth-child(2) .avatar{background:#e1f1ee;color:#256a60}.customer:nth-child(3) .avatar{background:#fff0dc;color:#946322}
.customer-name{font:inherit;font-size:13px;font-weight:650;border:0;background:none;color:inherit;cursor:pointer;padding:0;text-align:left}
.customer-name:hover{text-decoration:underline}.customer-name:focus-visible{outline:2px solid #7161bf;outline-offset:4px}
.customer-type{font-size:10px;color:var(--text-muted,#687078);margin:4px 0 0}
.stats{display:grid;grid-template-columns:.7fr 1fr 1fr;gap:8px;border-top:1px solid var(--border-color,#eeedf2);padding-top:16px;margin-top:18px}
.stats strong,.stats span{display:block}.stats strong{font-size:16px;letter-spacing:-.35px;font-weight:650}
.stats span{font-size:10px;color:var(--text-muted,#687078);margin-top:5px}
@media(max-width:950px){.customers{grid-template-columns:1fr}.section-title{flex-wrap:wrap}}
''', script='''
(async () => {
 const container = root_element.querySelector('.customers');
 try {
  const names = ['FITIT Aurora Clinic','FITIT Brisa Architecture','FITIT Cedar Academy'];
  const [customers, orders, invoices] = await Promise.all([
   frappe.db.get_list('Customer',{fields:['name','customer_name'],filters:{name:['in',names]},limit:3}),
   frappe.db.get_list('Sales Order',{fields:['customer','name'],filters:{customer:['in',names],docstatus:1},limit:1000}),
   frappe.db.get_list('Sales Invoice',{fields:['customer','base_grand_total','outstanding_amount'],filters:{customer:['in',names],docstatus:1},limit:1000})
  ]);
  const money = n => new Intl.NumberFormat('en-IE',{style:'currency',currency:'EUR',maximumFractionDigits:0}).format(n);
  container.replaceChildren();
  for (const name of names) {
   const customer = customers.find(c=>c.name===name);
   if (!customer) continue;
   const card = root_element.querySelector('#customer-card').content.cloneNode(true);
   const label = customer.customer_name.replace(/^FITIT /,'');
   card.querySelector('.avatar').textContent = label.split(' ').slice(0,2).map(x=>x[0]).join('');
   const button = card.querySelector('.customer-name');
   button.textContent=label; button.addEventListener('click',()=>frappe.set_route('Form','Customer',name));
   const rows=invoices.filter(r=>r.customer===name);
   card.querySelector('.orders').textContent=orders.filter(r=>r.customer===name).length;
   card.querySelector('.sales').textContent=money(rows.reduce((s,r)=>s+Number(r.base_grand_total),0));
   card.querySelector('.balance').textContent=money(rows.reduce((s,r)=>s+Number(r.outstanding_amount),0));
   container.append(card);
  }
  if (!container.children.length) container.textContent='No customer records available for your account.';
 } catch (error) { container.textContent='Customer activity could not be loaded. Refresh the workspace to try again.'; console.error('FITIT customer cards',error); }
})();''')

    card_specs = [
        ("Sales revenue", "Sales Invoice", "Sum", "base_grand_total", [["Sales Invoice", "docstatus", "=", 1]], "#5944a1", "#f1edfc"),
        ("Sales orders", "Sales Order", "Count", None, [["Sales Order", "docstatus", "=", 1]], "#276f66", "#edf7f4"),
        ("Active customers", "Customer", "Count", None, [], "#3f65a0", "#eff4fc"),
        ("Amount to collect", "Sales Invoice", "Sum", "outstanding_amount", [["Sales Invoice", "docstatus", "=", 1]], "#9c6425", "#fff5e8"),
        ("Sales leads", "Lead", "Count", None, [], "#5944a1", "#ffffff"),
        ("Suppliers", "Supplier", "Count", None, [], "#276f66", "#ffffff"),
        ("Products & services", "Item", "Count", None, [], "#3f65a0", "#ffffff"),
        ("Paid invoices", "Sales Invoice", "Count", None, [["Sales Invoice", "status", "=", "Paid"]], "#276f66", "#ffffff"),
    ]
    cards = []
    for label, doctype, function, field, extra, color, background in card_specs:
        name = "FITIT " + label
        save("Number Card", name, label=label, type="Document Type", document_type=doctype,
             function=function, aggregate_function_based_on=field, filters_json=filters(doctype, *extra),
             is_public=1, is_standard=0, show_percentage_stats=0, show_full_number=1,
             currency="EUR" if function == "Sum" else None, color=color, background_color=background)
        cards.append({"number_card_name": name, "label": label})

    dates = frappe.get_all("Sales Order", filters={"name": ["like", "FITIT%"], "docstatus": 1},
                           fields=["transaction_date"], order_by="transaction_date")
    save("Dashboard Chart", "FITIT Sales Performance", chart_name="FITIT Sales Performance",
         chart_type="Sum", document_type="Sales Order", based_on="transaction_date",
         value_based_on="base_grand_total", timeseries=1, timespan="Select Date Range",
         from_date=dates[0].transaction_date, to_date=dates[-1].transaction_date,
         time_interval="Daily", type="Line", currency="EUR", color="#6250a4", is_public=1,
         filters_json=filters("Sales Order", ["Sales Order", "docstatus", "=", 1]),
         custom_options=json.dumps({"colors": ["#6250a4"], "lineOptions": {"regionFill": 1, "hideDots": 0},
                                    "axisOptions": {"shortenYAxisNumbers": 1}}))
    save("Dashboard Chart", "FITIT Invoice Status", chart_name="FITIT Invoice Status",
         chart_type="Group By", document_type="Sales Invoice", group_by_based_on="status",
         group_by_type="Count", number_of_groups=8, type="Donut", is_public=1, currency="",
         filters_json=filters("Sales Invoice"),
         custom_options=json.dumps({"colors": ["#6250a4", "#54a998", "#e5b66f"]}))
    save("Dashboard Chart", "FITIT Revenue by Customer", chart_name="FITIT Revenue by Customer",
         chart_type="Group By", document_type="Sales Invoice", group_by_based_on="customer",
         group_by_type="Sum", aggregate_function_based_on="base_grand_total", number_of_groups=6,
         type="Bar", is_public=1, currency="EUR", color="#54a998",
         filters_json=filters("Sales Invoice", ["Sales Invoice", "docstatus", "=", 1]))

    content = []
    def block(kind, key, value, col=12):
        content.append({"id": f"fitit-{len(content)}", "type": kind, "data": {key: value, "col": col}})

    block("custom_block", "custom_block_name", "FITIT Activity Header")
    for card in cards[:4]:
        block("number_card", "number_card_name", card["label"], 3)
    block("custom_block", "custom_block_name", "FITIT Featured Customers")
    for card in cards[4:]:
        block("number_card", "number_card_name", card["label"], 3)
    charts = [
        {"chart_name": "FITIT Sales Performance", "label": "Sales performance"},
        {"chart_name": "FITIT Invoice Status", "label": "Invoice status"},
        {"chart_name": "FITIT Revenue by Customer", "label": "Revenue by customer"},
    ]
    block("chart", "chart_name", "Sales performance", 8)
    block("chart", "chart_name", "Invoice status", 4)
    block("chart", "chart_name", "Revenue by customer", 8)
    block("quick_list", "quick_list_name", "Open tasks", 4)
    shortcuts = [{"label": label, "type": "DocType", "link_to": dt, "doc_view": "List",
                  "color": "Purple", "stats_filter": json.dumps([["name", "like", "FITIT%"]])}
                 for label, dt in [("Customers", "Customer"), ("Sales orders", "Sales Order"),
                                   ("Invoices", "Sales Invoice"), ("Inventory", "Item")]]
    for shortcut in shortcuts:
        block("shortcut", "shortcut_name", shortcut["label"], 3)
    workspace = save("Workspace", PAGE, label=PAGE, title=PAGE, module="Selling", public=1,
                     standard=0, icon="chart-line", indicator_color="purple", content=json.dumps(content),
                     number_cards=cards, charts=charts, shortcuts=shortcuts,
                     quick_lists=[{"label": "Open tasks", "document_type": "Task",
                                   "quick_list_filter": json.dumps([["name", "like", "FITIT%"], ["status", "!=", "Completed"]])}],
                     custom_blocks=[{"custom_block_name": name, "label": name}
                                    for name in ("FITIT Activity Header", "FITIT Featured Customers")])
    from frappe.desk.doctype.workspace.workspace import add_to_sidebar
    add_to_sidebar(workspace)
    print("Workspace created: /desk/selling/fitit-overview", flush=True)


def verify():
    from frappe.desk.doctype.number_card.number_card import get_result
    from frappe.desk.doctype.dashboard_chart.dashboard_chart import get

    workspace = frappe.get_doc("Workspace", PAGE)
    assert len(workspace.number_cards) == 8
    for row in workspace.number_cards:
        card = frappe.get_doc("Number Card", row.number_card_name)
        value = get_result(card.as_json(), card.filters_json)
        assert value > 0, (card.name, value)
        print(f"{card.label}: {value}", flush=True)
    for row in workspace.charts:
        data = get(chart_name=row.chart_name, refresh=1)
        assert data and data["labels"] and any(sum(s["values"]) > 0 for s in data["datasets"]), data
        print(f"{row.label}: {len(data['labels'])} points", flush=True)


if __name__ == "__main__":
    main()
