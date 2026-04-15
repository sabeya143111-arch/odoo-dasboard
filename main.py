from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import xmlrpc.client
from datetime import datetime, timedelta
from typing import Optional

# ---------- ODOO CONFIG ----------
ODOO_URL = "https://db.swag.com.sa"
ODOO_DB = "db2"
ODOO_USER = "ziad.m@swag.com.sa"
ODOO_PASSWORD = "7cda7ec6fccb6afc78fd1968d93b09240572ee2b"

# ---------- FIELD NAME CONSTANTS ----------
BRAND_FIELD = "product_brand_id"
CATEG_FIELD = "categ_id"

# ---------- FASTAPI APP ----------
app = FastAPI()

# Frontend origins (prod + preview + local)
ALLOWED_ORIGINS = [
    "https://odoo-dasboard.vercel.app",   # main prod
    "https://odoo-dasboard-6zuaylfms-tariques-projects-74503042.vercel.app",  # current preview
    "http://localhost:3000",             # local dev (Next/React)
    "http://localhost:5173",             # local dev (Vite)
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,   # testing ke liye ["*"] bhi kar sakte ho
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- LAZY ODOO CONNECTION ----------
def get_odoo():
    try:
        common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
        uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_PASSWORD, {})
        if not uid:
            raise HTTPException(status_code=401, detail="Odoo auth failed")
        models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")
        return uid, models
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Odoo connection error: {str(e)}")


# ---------- SHARED HELPER ----------
def odoo_search_read(model, domain, fields, limit=5000, order="id desc"):
    uid, models = get_odoo()
    kw = {"fields": fields, "order": order}
    if limit:
        kw["limit"] = limit
    return models.execute_kw(
        ODOO_DB, uid, ODOO_PASSWORD,
        model, "search_read",
        [domain],
        kw
    )


def _build_tmpl_map(tmpl_ids: list) -> dict:
    if not tmpl_ids:
        return {}
    uid, models = get_odoo()
    tmpl_map = {}
    try:
        tmpl_records = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            "product.template", "read",
            [tmpl_ids],
            {"fields": ["id", CATEG_FIELD, BRAND_FIELD]}
        )
        for t in tmpl_records:
            categ = t.get(CATEG_FIELD)
            brand = t.get(BRAND_FIELD)
            tmpl_map[t["id"]] = {
                "category": categ[1] if categ else "Unknown",
                "brand":    brand[1] if brand else "Unknown",
            }
    except Exception:
        pass
    return tmpl_map


def _build_product_tmpl_map(prod_ids: list) -> dict:
    if not prod_ids:
        return {}
    uid, models = get_odoo()
    prod_map = {}
    try:
        prod_records = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            "product.product", "read",
            [prod_ids],
            {"fields": ["id", "name", "product_tmpl_id", CATEG_FIELD, BRAND_FIELD]}
        )

        tmpl_ids = list({
            p["product_tmpl_id"][0]
            for p in prod_records
            if p.get("product_tmpl_id")
        })
        tmpl_map = _build_tmpl_map(tmpl_ids)

        for p in prod_records:
            tmpl_raw = p.get("product_tmpl_id")
            tmpl_id  = tmpl_raw[0] if tmpl_raw else None
            tmpl_info = tmpl_map.get(tmpl_id, {})

            brand_raw = p.get(BRAND_FIELD)
            brand = (
                tmpl_info.get("brand")
                or (brand_raw[1] if brand_raw else None)
                or "Unknown"
            )

            categ_raw = p.get(CATEG_FIELD)
            category = (
                tmpl_info.get("category")
                or (categ_raw[1] if categ_raw else None)
                or "Unknown"
            )

            prod_map[p["id"]] = {
                "name":     p.get("name", "Unknown"),
                "brand":    brand,
                "category": category,
                "tmpl_id":  tmpl_id,
            }
    except Exception:
        pass
    return prod_map


def _build_velocity_map(var_ids: list, uid, models) -> dict:
    if not var_ids:
        return {}
    date_30_ago = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    vel_map = {}
    try:
        variants = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            "product.product", "read",
            [var_ids],
            {"fields": ["id", "product_tmpl_id"]}
        )
        var_to_tmpl = {}
        for v in variants:
            raw = v.get("product_tmpl_id")
            tid = raw[0] if isinstance(raw, list) else raw
            var_to_tmpl[v["id"]] = tid

        so_lines = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            "sale.order.line", "search_read",
            [[
                ("product_id", "in", var_ids),
                ("order_id.date_order", ">=", f"{date_30_ago} 00:00:00"),
                ("order_id.state", "in", ["sale", "done"]),
            ]],
            {"fields": ["product_id", "product_uom_qty"], "limit": 50000}
        )
        for sl in so_lines:
            pid_raw = sl.get("product_id")
            vid = pid_raw[0] if isinstance(pid_raw, list) else pid_raw
            tid = var_to_tmpl.get(vid)
            if tid:
                vel_map[tid] = vel_map.get(tid, 0) + float(sl.get("product_uom_qty") or 0)
    except Exception:
        pass
    return vel_map


# ---------- HEALTH CHECK ----------
@app.get("/")
def root():
    return {"status": "SWAG Dashboard API running"}


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------- STOCK ENDPOINT ----------
@app.get("/api/stock")
def get_stock():
    uid, models = get_odoo()

    fields = ["product_id", "product_tmpl_id", "location_id", "quantity", "reserved_quantity"]
    quants = models.execute_kw(
        ODOO_DB, uid, ODOO_PASSWORD,
        "stock.quant", "search_read",
        [[["quantity", ">", 0]]],
        {"fields": fields, "limit": 2000}
    )

    tmpl_ids = list({q["product_tmpl_id"][0] for q in quants if q.get("product_tmpl_id")})
    tmpl_map = _build_tmpl_map(tmpl_ids)

    var_ids = list({q["product_id"][0] for q in quants if q.get("product_id")})
    var_to_tmpl_quant = {}
    for q in quants:
        if q.get("product_id") and q.get("product_tmpl_id"):
            var_to_tmpl_quant[q["product_id"][0]] = q["product_tmpl_id"][0]

    vel_map = {}
    if var_ids:
        date_30_ago = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        try:
            so_lines = models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD,
                "sale.order.line", "search_read",
                [[
                    ("product_id", "in", var_ids),
                    ("order_id.date_order", ">=", f"{date_30_ago} 00:00:00"),
                    ("order_id.state", "in", ["sale", "done"]),
                ]],
                {"fields": ["product_id", "product_uom_qty"], "limit": 50000}
            )
            for sl in so_lines:
                pid_raw = sl.get("product_id")
                vid = pid_raw[0] if isinstance(pid_raw, list) else pid_raw
                tid = var_to_tmpl_quant.get(vid)
                if tid:
                    vel_map[tid] = vel_map.get(tid, 0) + float(sl.get("product_uom_qty") or 0)
        except Exception:
            pass

    total = []
    branch = []
    reorder = []

    tmpl_qty = {}
    for q in quants:
        tid = q["product_tmpl_id"][0] if q.get("product_tmpl_id") else None
        if tid:
            tmpl_qty[tid] = tmpl_qty.get(tid, 0) + float(q["quantity"] or 0)

    seen_tmpl = set()
    for q in quants:
        product_name = q["product_id"][1]
        product_id = q["product_id"][0]
        location_name = q["location_id"][1] if q["location_id"] else "Unknown"
        qty = float(q["quantity"] or 0)
        tmpl_id = q["product_tmpl_id"][0] if q.get("product_tmpl_id") else None
        info = tmpl_map.get(tmpl_id, {})
        brand_name = info.get("brand", "Unknown")
        category_name = info.get("category", "Unknown")

        sold_30 = vel_map.get(tmpl_id, 0) if tmpl_id else 0
        daily_vel = round(sold_30 / 30.0, 3)
        on_hand_total = tmpl_qty.get(tmpl_id, qty) if tmpl_id else qty
        days_left = round(on_hand_total / daily_vel, 1) if daily_vel > 0 else None

        if tmpl_id not in seen_tmpl:
            seen_tmpl.add(tmpl_id)
            total.append({
                "system": "SWAG",
                "brand": brand_name,
                "category": category_name,
                "model": str(product_id),
                "product": product_name,
                "on_hand": on_hand_total,
                "sold_30d": round(sold_30, 1),
                "daily_velocity": daily_vel,
                "days_left": days_left if days_left is not None else "∞",
                "sale_price": 0,
            })

        branch.append({
            "system": "SWAG",
            "branch": location_name,
            "brand": brand_name,
            "category": category_name,
            "model": str(product_id),
            "on_hand": qty,
        })

        if on_hand_total < 5:
            priority = "Critical" if on_hand_total == 0 else "Low"
            suggest = 20
            reorder.append({
                "system": "SWAG",
                "brand": brand_name,
                "category": category_name,
                "model": str(product_id),
                "product": product_name,
                "on_hand": on_hand_total,
                "sold_30d": round(sold_30, 1),
                "daily_velocity": daily_vel,
                "days_left": days_left if days_left is not None else "∞",
                "suggest": suggest,
                "priority": priority,
            })

    return {"total": total, "branch": branch, "reorder": reorder}


# ---------- PURCHASE ENDPOINT ----------
@app.get("/api/purchase")
def get_purchase():
    uid, models = get_odoo()

    domain = [["state", "in", ["purchase", "done"]]]
    fields = [
        "order_id",
        "date_order",
        "partner_id",
        "product_id",
        "product_qty",
        "price_unit",
        "price_subtotal",
        "currency_id",
    ]
    lines = odoo_search_read(
        "purchase.order.line",
        domain,
        fields,
        limit=0,
        order="id desc",
    )

    prod_ids = list({line["product_id"][0] for line in lines if line.get("product_id")})
    prod_map = _build_product_tmpl_map(prod_ids)

    date_30_ago = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    vel_by_prod = {}
    if prod_ids:
        try:
            so_lines = models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD,
                "sale.order.line", "search_read",
                [[
                    ("product_id", "in", prod_ids),
                    ("order_id.date_order", ">=", f"{date_30_ago} 00:00:00"),
                    ("order_id.state", "in", ["sale", "done"]),
                ]],
                {"fields": ["product_id", "product_uom_qty"], "limit": 50000}
            )
            for sl in so_lines:
                pid_raw = sl.get("product_id")
                pid = pid_raw[0] if isinstance(pid_raw, list) else pid_raw
                vel_by_prod[pid] = vel_by_prod.get(pid, 0) + float(sl.get("product_uom_qty") or 0)
        except Exception:
            pass

    stock_by_prod = {}
    if prod_ids:
        try:
            quants = models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD,
                "stock.quant", "search_read",
                [[("product_id", "in", prod_ids), ("location_id.usage", "=", "internal")]],
                {"fields": ["product_id", "quantity"], "limit": 50000}
            )
            for q in quants:
                pid_raw = q.get("product_id")
                pid = pid_raw[0] if isinstance(pid_raw, list) else pid_raw
                stock_by_prod[pid] = stock_by_prod.get(pid, 0) + float(q.get("quantity") or 0)
        except Exception:
            pass

    purchases = []
    for line in lines:
        prod_id = line["product_id"][0] if line.get("product_id") else None
        info = prod_map.get(prod_id, {})
        product_name = info.get("name") or (line["product_id"][1] if line.get("product_id") else "Unknown")
        category_name = info.get("category", "Unknown")
        brand_name = info.get("brand", "Unknown")

        sold_30 = vel_by_prod.get(prod_id, 0)
        daily_vel = round(sold_30 / 30.0, 3)
        on_hand = stock_by_prod.get(prod_id, 0)
        days_left = round(on_hand / daily_vel, 1) if daily_vel > 0 else None

        po_qty = float(line.get("product_qty") or 0)
        days_cover = round(po_qty / daily_vel, 1) if daily_vel > 0 else None

        currency_name = (
            line.get("currency_id")[1]
            if line.get("currency_id")
            else "SAR"
        )

        purchases.append({
            "date": (line.get("date_order") or "")[:10],
            "po": line["order_id"][1],
            "vendor": line["partner_id"][1],
            "currency": currency_name,
            "brand": brand_name,
            "category": category_name,
            "model": product_name,
            "qty": po_qty,
            "unit_price": float(line.get("price_unit") or 0),
            "subtotal": float(line.get("price_subtotal") or 0),
            "on_hand": on_hand,
            "sold_30d": round(sold_30, 1),
            "daily_velocity": daily_vel,
            "days_left": days_left if days_left is not None else "∞",
            "days_cover_by_po": days_cover if days_cover is not None else "∞",
        })

    total_spend = sum(p["subtotal"] for p in purchases)
    total_qty = sum(p["qty"] for p in purchases)
    unique_vendors = list({p["vendor"] for p in purchases})
    by_vendor = {}
    for p in purchases:
        by_vendor[p["vendor"]] = by_vendor.get(p["vendor"], 0) + p["subtotal"]
    top_vendor = max(by_vendor, key=by_vendor.get) if by_vendor else "—"
    by_brand = {}
    for p in purchases:
        by_brand[p["brand"]] = by_brand.get(p["brand"], 0) + p["subtotal"]
    by_category = {}
    for p in purchases:
        by_category[p["category"]] = by_category.get(p["category"], 0) + p["subtotal"]

    return {
        "purchases": purchases,
        "summary": {
            "total_spend": round(total_spend, 2),
            "total_qty": round(total_qty, 1),
            "vendor_count": len(unique_vendors),
            "po_line_count": len(purchases),
            "top_vendor": top_vendor,
            "by_vendor": by_vendor,
            "by_brand": by_brand,
            "by_category": by_category,
        }
    }


# ---------- SALES ENDPOINT ----------
@app.get("/api/sales")
def get_sales(
    from_date: Optional[str] = Query(None, description="Start date YYYY-MM-DD (inclusive)"),
    to_date:   Optional[str] = Query(None, description="End date YYYY-MM-DD (inclusive)"),
):
    uid, models = get_odoo()

    domain = [["state", "in", ["sale", "done"]]]

    if from_date:
        domain.append(("order_id.date_order", ">=", f"{from_date} 00:00:00"))
    if to_date:
        domain.append(("order_id.date_order", "<=", f"{to_date} 23:59:59"))

    fields = [
        "order_id",
        "order_partner_id",
        "product_id",
        "product_uom_qty",
        "price_unit",
        "price_subtotal",
        "create_date",
    ]

    lines = odoo_search_read(
        "sale.order.line",
        domain,
        fields,
        limit=0,
        order="id desc",
    )

    prod_ids = list({line["product_id"][0] for line in lines if line.get("product_id")})
    prod_map = _build_product_tmpl_map(prod_ids)

    date_30_ago = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    vel_by_prod = {}
    if prod_ids:
        try:
            so_vel_lines = models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD,
                "sale.order.line", "search_read",
                [[
                    ("product_id", "in", prod_ids),
                    ("order_id.date_order", ">=", f"{date_30_ago} 00:00:00"),
                    ("order_id.state", "in", ["sale", "done"]),
                ]],
                {"fields": ["product_id", "product_uom_qty"], "limit": 50000}
            )
            for sl in so_vel_lines:
                pid_raw = sl.get("product_id")
                pid = pid_raw[0] if isinstance(pid_raw, list) else pid_raw
                vel_by_prod[pid] = vel_by_prod.get(pid, 0) + float(sl.get("product_uom_qty") or 0)
        except Exception:
            pass

    stock_by_prod = {}
    if prod_ids:
        try:
            quants = models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD,
                "stock.quant", "search_read",
                [[("product_id", "in", prod_ids), ("location_id.usage", "=", "internal")]],
                {"fields": ["product_id", "quantity"], "limit": 50000}
            )
            for q in quants:
                pid_raw = q.get("product_id")
                pid = pid_raw[0] if isinstance(pid_raw, list) else pid_raw
                stock_by_prod[pid] = stock_by_prod.get(pid, 0) + float(q.get("quantity") or 0)
        except Exception:
            pass

    sales = []
    for line in lines:
        prod_raw = line.get("product_id")
        prod_id  = prod_raw[0] if prod_raw else None

        info = prod_map.get(prod_id, {})
        product_name  = info.get("name") or (prod_raw[1] if prod_raw else "Unknown")
        category_name = info.get("category", "Unknown")
        brand_name    = info.get("brand", "Unknown")

        sold_30   = vel_by_prod.get(prod_id, 0)
        daily_vel = round(sold_30 / 30.0, 3)
        on_hand   = stock_by_prod.get(prod_id, 0)
        days_left = round(on_hand / daily_vel, 1) if daily_vel > 0 else None

        partner_raw = line.get("order_partner_id")
        customer = partner_raw[1] if partner_raw else "Unknown"

        sales.append({
            "date":          (line.get("create_date") or "")[:10],
            "so":            line["order_id"][1],
            "customer":      customer,
            "brand":         brand_name,
            "category":      category_name,
            "model":         product_name,
            "qty":           float(line.get("product_uom_qty") or 0),
            "unit_price":    float(line.get("price_unit") or 0),
            "subtotal":      float(line.get("price_subtotal") or 0),
            "on_hand":       on_hand,
            "sold_30d":      round(sold_30, 1),
            "daily_velocity": daily_vel,
            "days_left":     days_left if days_left is not None else "∞",
        })

    total_revenue    = sum(s["subtotal"] for s in sales)
    total_qty        = sum(s["qty"] for s in sales)
    unique_customers = list({s["customer"] for s in sales})

    by_customer = {}
    for s in sales:
        by_customer[s["customer"]] = by_customer.get(s["customer"], 0) + s["subtotal"]
    top_customer = max(by_customer, key=by_customer.get) if by_customer else "—"

    by_brand = {}
    for s in sales:
        by_brand[s["brand"]] = by_brand.get(s["brand"], 0) + s["subtotal"]

    by_category = {}
    for s in sales:
        by_category[s["category"]] = by_category.get(s["category"], 0) + s["subtotal"]

    return {
        "sales": sales,
        "summary": {
            "total_revenue":   round(total_revenue, 2),
            "total_qty":       round(total_qty, 1),
            "customer_count":  len(unique_customers),
            "so_line_count":   len(sales),
            "top_customer":    top_customer,
            "by_customer":     by_customer,
            "by_brand":        by_brand,
            "by_category":     by_category,
        }
    }


# ---------- ESTIMATE ENDPOINT ----------
@app.get("/api/estimate")
def get_estimate():
    uid, models = get_odoo()

    quants = models.execute_kw(
        ODOO_DB, uid, ODOO_PASSWORD,
        "stock.quant", "search_read",
        [[["location_id.usage", "=", "internal"]]],
        {"fields": ["product_id", "product_tmpl_id", "quantity"], "limit": 5000}
    )

    tmpl_qty = {}
    tmpl_to_var = {}
    for q in quants:
        if q.get("product_tmpl_id") and q.get("product_id"):
            tid = q["product_tmpl_id"][0]
            vid = q["product_id"][0]
            tmpl_qty[tid] = tmpl_qty.get(tid, 0) + float(q["quantity"] or 0)
            tmpl_to_var.setdefault(tid, []).append(vid)

    var_ids = [q["product_id"][0] for q in quants if q.get("product_id")]
    date_30_ago = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

    vel_map = {}
    if var_ids:
        try:
            so_lines = models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD,
                "sale.order.line", "search_read",
                [[
                    ("product_id", "in", var_ids),
                    ("order_id.date_order", ">=", f"{date_30_ago} 00:00:00"),
                    ("order_id.state", "in", ["sale", "done"]),
                ]],
                {"fields": ["product_id", "product_uom_qty"], "limit": 50000}
            )
            v2t = {}
            for q in quants:
                if q.get("product_id") and q.get("product_tmpl_id"):
                    v2t[q["product_id"][0]] = q["product_tmpl_id"][0]
            for sl in so_lines:
                pid_raw = sl.get("product_id")
                vid = pid_raw[0] if isinstance(pid_raw, list) else pid_raw
                tid = v2t.get(vid)
                if tid:
                    vel_map[tid] = vel_map.get(tid, 0) + float(sl.get("product_uom_qty") or 0)
        except Exception:
            pass

    tmpl_ids = list(tmpl_qty.keys())
    tmpl_map = _build_tmpl_map(tmpl_ids)

    tmpl_names = {}
    if tmpl_ids:
        try:
            tmpl_records = models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD,
                "product.template", "read",
                [tmpl_ids],
                {"fields": ["id", "name", "default_code"]}
            )
            for t in tmpl_records:
                tmpl_names[t["id"]] = {
                    "name": t.get("name", ""),
                    "code": t.get("default_code", ""),
                }
        except Exception:
            pass

    estimates = []
    for tid, on_hand in tmpl_qty.items():
        sold_30 = vel_map.get(tid, 0)
        daily_vel = round(sold_30 / 30.0, 3)
        days_left = round(on_hand / daily_vel, 1) if daily_vel > 0 else None
        info = tmpl_map.get(tid, {})
        name_info = tmpl_names.get(tid, {})

        estimates.append({
            "product":       name_info.get("name", ""),
            "code":          name_info.get("code", ""),
            "brand":         info.get("brand", "Unknown"),
            "category":      info.get("category", "Unknown"),
            "on_hand":       on_hand,
            "sold_30d":      round(sold_30, 1),
            "daily_velocity": daily_vel,
            "days_left":     days_left if days_left is not None else "∞",
            "priority": (
                "Critical" if (days_left is not None and days_left < 7)
                else "Warning" if (days_left is not None and days_left < 14)
                else "OK"
            ),
        })

    estimates.sort(key=lambda x: (
        float(x["days_left"]) if x["days_left"] != "∞" else 9999
    ))

    return {"estimates": estimates, "total": len(estimates)}
