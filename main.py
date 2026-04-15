from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import xmlrpc.client

# ---------- ODOO CONFIG ----------
ODOO_URL = "https://db.swag.com.sa"
ODOO_DB = "db2"
ODOO_USER = "ziad.m@swag.com.sa"
ODOO_PASSWORD = "7cda7ec6fccb6afc78fd1968d93b09240572ee2b"

# ---------- FASTAPI APP ----------
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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


def odoo_search_read(model, domain, fields, limit=2000):
    uid, models = get_odoo()
    return models.execute_kw(
        ODOO_DB, uid, ODOO_PASSWORD,
        model, "search_read",
        [domain],
        {"fields": fields, "limit": limit}
    )


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
    # Step 1: read stock quants (including product_tmpl_id for brand/category lookup)
    fields = ["product_id", "product_tmpl_id", "location_id", "quantity", "reserved_quantity"]
    quants = odoo_search_read("stock.quant", [["quantity", ">", 0]], fields, limit=2000)

    # Step 2: collect unique template IDs for one batched read
    tmpl_ids = list({q["product_tmpl_id"][0] for q in quants if q.get("product_tmpl_id")})

    # Step 3: batched read from product.template
    tmpl_map = {}  # template_id -> {"brand": str, "category": str}
    if tmpl_ids:
        uid, models = get_odoo()
        try:
            tmpl_records = models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD,
                "product.template", "read",
                [tmpl_ids],
                {"fields": ["id", "categ_id", "product_brand_id"]}
            )
            for t in tmpl_records:
                categ = t.get("categ_id")
                brand = t.get("product_brand_id")
                tmpl_map[t["id"]] = {
                    "category": categ[1] if categ else "Unknown",
                    "brand": brand[1] if brand else "Unknown",
                }
        except Exception:
            pass  # fall back to Unknown if field doesn't exist on this Odoo instance

    total = []
    branch = []
    reorder = []

    for q in quants:
        product_name = q["product_id"][1]
        product_id = q["product_id"][0]
        location_name = q["location_id"][1] if q["location_id"] else "Unknown"
        qty = q["quantity"]

        tmpl_id = q["product_tmpl_id"][0] if q.get("product_tmpl_id") else None
        info = tmpl_map.get(tmpl_id, {})
        brand_name = info.get("brand", "Unknown")
        category_name = info.get("category", "Unknown")

        total.append({
            "system": "SWAG",
            "brand": brand_name,
            "category": category_name,
            "model": str(product_id),
            "product": product_name,
            "on_hand": qty,
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
        if qty < 5:
            reorder.append({
                "system": "SWAG",
                "brand": brand_name,
                "category": category_name,
                "model": str(product_id),
                "product": product_name,
                "on_hand": qty,
                "suggest": 20,
                "priority": "Critical" if qty == 0 else "Low",
            })

    return {"total": total, "branch": branch, "reorder": reorder}


# ---------- PURCHASE ENDPOINT ----------
@app.get("/api/purchase")
def get_purchase():
    # Step 1: read purchase order lines (approved POs only)
    domain = [["state", "in", ["purchase", "done"]]]
    fields = ["order_id", "date_order", "partner_id", "product_id", "product_qty", "price_subtotal"]
    lines = odoo_search_read("purchase.order.line", domain, fields, limit=2000)

    # Step 2: collect unique product IDs for one batched read
    prod_ids = list({line["product_id"][0] for line in lines if line.get("product_id")})

    # Step 3: batched read from product.product
    prod_map = {}  # product_id -> {"brand": str, "category": str, "name": str}
    if prod_ids:
        uid, models = get_odoo()
        try:
            prod_records = models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD,
                "product.product", "read",
                [prod_ids],
                {"fields": ["id", "name", "categ_id", "product_brand_id"]}
            )
            for p in prod_records:
                categ = p.get("categ_id")
                brand = p.get("product_brand_id")
                prod_map[p["id"]] = {
                    "name": p.get("name", "Unknown"),
                    "category": categ[1] if categ else "Unknown",
                    "brand": brand[1] if brand else "Unknown",
                }
        except Exception:
            pass  # fall back gracefully if field missing

    purchases = []
    for line in lines:
        prod_id = line["product_id"][0] if line.get("product_id") else None
        info = prod_map.get(prod_id, {})
        product_name = info.get("name") or (line["product_id"][1] if line.get("product_id") else "Unknown")
        category_name = info.get("category", "Unknown")
        brand_name = info.get("brand", "Unknown")

        purchases.append({
            "date": (line.get("date_order") or "")[:10],
            "po": line["order_id"][1],
            "vendor": line["partner_id"][1],
            "brand": brand_name,
            "category": category_name,
            "model": product_name,
            "qty": line["product_qty"],
            "subtotal": line["price_subtotal"],
        })

    return {"purchases": purchases}


# ---------- SALES ENDPOINT ----------
@app.get("/api/sales")
def get_sales():
    domain = [["state", "in", ["sale", "done"]]]
    fields = ["order_id", "order_partner_id", "product_id", "product_uom_qty", "price_subtotal", "create_date"]
    lines = odoo_search_read("sale.order.line", domain, fields, limit=2000)
    sales = []
    for line in lines:
        sales.append({
            "date": (line.get("create_date") or "")[:10],
            "so": line["order_id"][1],
            "customer": line["order_partner_id"][1],
            "model": line["product_id"][1],
            "qty": line["product_uom_qty"],
            "subtotal": line["price_subtotal"],
        })
    return {"sales": sales}
