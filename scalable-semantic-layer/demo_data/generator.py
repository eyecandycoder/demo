"""Synthetic supply chain data for the demo. Deterministic (fixed seed).

Pure Python so the same rows load into SQLite (local tests) and Spark (Databricks).

A story is planted so cross-domain questions have a real answer:
  * Late July - mid August: the North plant (PL-NOR) under-produces Foods SKUs.
  * August: North DC (DC-NOR) Snacks demand spikes ~60%, and the forecast misses it.
  * So North DC inventory falls below safety stock, shipments leave late, and OTD drops.
"""
from __future__ import annotations

import datetime as dt
import random
from collections import defaultdict

START = dt.date(2026, 4, 1)
DAYS = 183                      # 1 Apr - 30 Sep 2026 (FY27 H1)
SEED = 42


def _dk(d: dt.date) -> int:
    return int(d.strftime("%Y%m%d"))


def _dates():
    return [START + dt.timedelta(days=i) for i in range(DAYS)]


def _disrupted_production(d: dt.date) -> bool:
    return dt.date(2026, 7, 20) <= d <= dt.date(2026, 8, 20)


def _north_august(d: dt.date) -> bool:
    return d.month == 8


# ----------------------------------------------------------------- dimensions
def dim_calendar():
    rows = []
    for i, d in enumerate(_dates()):
        q = (d.month - 4) // 3 + 1
        rows.append({
            "date_key": _dk(d), "calendar_date": d.isoformat(),
            "fiscal_week": f"FY27-W{i // 7 + 1:02d}",
            "week_start_key": _dk(START + dt.timedelta(days=(i // 7) * 7)),
            "fiscal_month": d.strftime("%Y-%m"),
            "fiscal_quarter": f"FY27-Q{q}", "fiscal_year": "FY27",
        })
    return rows


PRODUCT_TREE = {
    "Foods": {"Snacks": ["Chips", "Biscuits"], "Beverages": ["Juices", "Tea"]},
    "Home Care": {"Laundry": ["Detergent Powder", "Liquid Detergent"], "Cleaning": ["Floor Cleaner", "Dishwash"]},
}


def dim_product():
    rows, n = [], 0
    rnd = random.Random(SEED)
    for bu, cats in PRODUCT_TREE.items():
        for cat, fams in cats.items():
            for fam in fams:
                for v in range(3):
                    n += 1
                    rows.append({"sku_id": f"SKU-{n:03d}", "sku_name": f"{fam} {['S', 'M', 'L'][v]}",
                                 "product_family": fam, "category": cat, "business_unit": bu,
                                 "unit_price": round(rnd.uniform(40, 400), 2)})
    return rows


def dim_location():
    return [
        {"location_id": "DC-NOR", "location_name": "Delhi DC",   "location_type": "DC",    "city": "Delhi",   "region": "North", "country": "India"},
        {"location_id": "DC-SOU", "location_name": "Chennai DC", "location_type": "DC",    "city": "Chennai", "region": "South", "country": "India"},
        {"location_id": "DC-EAS", "location_name": "Kolkata DC", "location_type": "DC",    "city": "Kolkata", "region": "East",  "country": "India"},
        {"location_id": "DC-WES", "location_name": "Mumbai DC",  "location_type": "DC",    "city": "Mumbai",  "region": "West",  "country": "India"},
        {"location_id": "PL-NOR", "location_name": "Baddi Plant", "location_type": "PLANT", "city": "Baddi",  "region": "North", "country": "India"},
        {"location_id": "PL-WES", "location_name": "Pune Plant",  "location_type": "PLANT", "city": "Pune",   "region": "West",  "country": "India"},
    ]


SEGMENTS = ["Modern Trade", "General Trade", "E-commerce Marketplace", "Institutional"]


def dim_customer():
    rnd = random.Random(SEED + 1)
    return [{"customer_id": f"CUST-{i:03d}", "customer_name": f"Customer {i:03d}", "segment": rnd.choice(SEGMENTS)}
            for i in range(1, 31)]


def dim_channel():
    return [
        {"channel_id": "CH-RET", "channel_name": "Retail",     "channel_group": "Offline"},
        {"channel_id": "CH-ECO", "channel_name": "E-commerce", "channel_group": "Online"},
        {"channel_id": "CH-B2B", "channel_name": "B2B",        "channel_group": "Offline"},
    ]


def dim_carrier():
    return [
        {"carrier_id": "CAR-01", "carrier_name": "Swift Road",     "transport_mode": "Road"},
        {"carrier_id": "CAR-02", "carrier_name": "Rapid Express",  "transport_mode": "Air"},
        {"carrier_id": "CAR-03", "carrier_name": "Coastal Freight", "transport_mode": "Road"},
    ]


SEGMENT_CHANNEL = {"Modern Trade": "CH-RET", "General Trade": "CH-RET",
                   "E-commerce Marketplace": "CH-ECO", "Institutional": "CH-B2B"}


# ---------------------------------------------------------------------- facts
def core_tables() -> dict[str, list[dict]]:
    rnd = random.Random(SEED)
    cal, products, locs, customers = dim_calendar(), dim_product(), dim_location(), dim_customer()
    dcs = [l["location_id"] for l in locs if l["location_type"] == "DC"]
    price = {p["sku_id"]: p["unit_price"] for p in products}
    cat = {p["sku_id"]: p["category"] for p in products}
    seg = {c["customer_id"]: c["segment"] for c in customers}
    skus = [p["sku_id"] for p in products]
    week_of = {r["date_key"]: r["week_start_key"] for r in cal}

    orders, shipments, weekly_actual = [], [], defaultdict(int)
    line = 0
    for d in _dates():
        for dc in dcs:
            for _ in range(rnd.randint(8, 14)):
                line += 1
                sku = rnd.choice(skus)
                cust = rnd.choice(customers)["customer_id"]
                qty = rnd.randint(5, 50)
                if dc == "DC-NOR" and _north_august(d) and cat[sku] == "Snacks":
                    qty = int(qty * 1.6)
                req = d + dt.timedelta(days=4)
                orders.append({"order_line_id": f"OL-{line:06d}", "order_date_key": _dk(d), "customer_id": cust,
                               "sku_id": sku, "ship_from_location_id": dc, "channel_id": SEGMENT_CHANNEL[seg[cust]],
                               "order_qty": qty, "unit_price": price[sku], "requested_date_key": _dk(req)})
                weekly_actual[(sku, dc, week_of[_dk(d)])] += qty

                disrupted = dc == "DC-NOR" and _north_august(d)
                ship = d + dt.timedelta(days=rnd.choice([2, 3, 3]) if disrupted else rnd.choice([1, 1, 2]))
                if ship > START + dt.timedelta(days=DAYS - 1):
                    continue
                carrier = rnd.choices(["CAR-01", "CAR-02", "CAR-03"], weights=[6, 1, 3] if disrupted else [4, 2, 4])[0]
                transit = {"CAR-01": rnd.choice([1, 2, 2, 3]), "CAR-02": 1, "CAR-03": rnd.choice([2, 2, 3])}[carrier]
                delivered = ship + dt.timedelta(days=transit)
                rate = {"CAR-01": 2.5, "CAR-02": 9.0, "CAR-03": 2.0}[carrier]
                shipments.append({"shipment_id": f"SH-{line:06d}", "order_line_id": f"OL-{line:06d}",
                                  "ship_date_key": _dk(ship), "delivered_date_key": _dk(delivered),
                                  "sku_id": sku, "ship_from_location_id": dc, "customer_id": cust,
                                  "carrier_id": carrier, "shipped_qty": qty,
                                  "on_time_flag": 1 if delivered <= req else 0,
                                  "freight_cost": round(qty * rate, 2)})

    forecast = []
    for (sku, dc, wk), actual in sorted(weekly_actual.items()):
        in_aug = str(wk)[4:6] == "08"
        base = actual / 1.6 if (dc == "DC-NOR" and in_aug and cat[sku] == "Snacks") else actual
        forecast.append({"sku_id": sku, "location_id": dc, "week_start_key": wk,
                         "forecast_units": max(0, round(base * rnd.uniform(0.85, 1.15))), "actual_units": actual})

    inventory = []
    for sku in skus:
        for dc in dcs:
            safety = rnd.randint(120, 220)
            on_hand = safety * rnd.uniform(1.4, 1.8)
            for d in _dates():
                target = safety * (0.6 if (dc == "DC-NOR" and (_north_august(d) or _disrupted_production(d))) else 1.6)
                on_hand = max(0.0, on_hand + (target - on_hand) * 0.25 + rnd.uniform(-15, 15))
                inventory.append({"snapshot_date_key": _dk(d), "sku_id": sku, "location_id": dc,
                                  "on_hand_units": int(on_hand), "safety_stock_units": safety})

    tickets, t = [], 0
    for d in _dates():
        for _ in range(rnd.randint(4, 9)):
            t += 1
            issue = rnd.choices(["Late delivery", "Damaged", "Wrong item", "Quality"],
                                weights=[5, 2, 1, 2] if d.month == 8 else [2, 2, 1, 2])[0]
            tickets.append({"ticket_id": f"TK-{t:05d}", "ticket_date_key": _dk(d),
                            "customer_id": rnd.choice(customers)["customer_id"], "sku_id": rnd.choice(skus),
                            "issue_type": issue, "resolution_hours": round(rnd.uniform(2, 72), 1)})

    return {
        "gold.dim_calendar": cal, "gold.dim_product": products, "gold.dim_location": locs,
        "gold.dim_customer": customers, "gold.dim_channel": dim_channel(), "gold.dim_carrier": dim_carrier(),
        "gold.fact_order_line": orders, "gold.fact_shipment": shipments,
        "gold.fact_forecast_weekly": forecast, "gold.fact_inventory_daily": inventory,
        "gold.fact_service_ticket": tickets,
    }


def manufacturing_tables() -> dict[str, list[dict]]:
    """Arrives later: Manufacturing uses material codes and its own machines."""
    rnd = random.Random(SEED + 7)
    products = dim_product()
    xref, machines, production = [], [], []
    for i, p in enumerate(products):
        xref.append({"material_code": f"MAT-{7000 + i}", "sku_id": p["sku_id"]})
    for plant in ["PL-NOR", "PL-WES"]:
        for m in range(1, 4):
            machines.append({"machine_id": f"{plant}-M{m}", "plant_id": plant,
                             "line_name": f"{plant} Line {'A' if m < 3 else 'B'}"})
    bu = {p["sku_id"]: p["business_unit"] for p in products}
    for d in _dates():
        for x in xref:
            plant = "PL-NOR" if bu[x["sku_id"]] == "Foods" else "PL-WES"
            machine = f"{plant}-M{rnd.randint(1, 3)}"
            planned = rnd.randint(80, 140)
            if plant == "PL-NOR" and _disrupted_production(d):
                good, scrap = int(planned * rnd.uniform(0.62, 0.78)), int(planned * rnd.uniform(0.06, 0.12))
            else:
                good, scrap = int(planned * rnd.uniform(0.93, 1.01)), int(planned * rnd.uniform(0.01, 0.03))
            production.append({"prod_date_key": _dk(d), "plant_id": plant, "material_code": x["material_code"],
                               "machine_id": machine, "planned_qty": planned, "good_qty": good, "scrap_qty": scrap})
    return {"gold.xref_material_sku": xref, "gold.dim_machine": machines, "gold.fact_production": production}


def load(runner, names, tables: dict[str, list[dict]]) -> dict[str, int]:
    counts = {}
    for logical, rows in tables.items():
        runner.write_table(names.physical(logical), rows)
        counts[logical] = len(rows)
    return counts
