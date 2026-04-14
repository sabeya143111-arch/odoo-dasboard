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

def odoo_search_read(model, domain, fields, limit=500):
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
    fields = ["product_id", "location_id", "quantity", "reserved_quantity"]
    quants = odoo_search_read("stock.quant", [["quantity", ">", 0]], fields, limit=500)
    total = []
    branch = []
    reorder = []
    for q in quants:
        product_name = q["product_id"][1]
        product_id = q["product_id"][0]
        location_name = q["location_id"][1] if q["location_id"] else "Unknown"
        qty = q["quantity"]
        total.append({
            "system": "SWAG",
            "brand": "Brand A",
            "category": "T-Shirts",
            "model": str(product_id),
            "product": product_name,
            "on_hand": qty,
            "sale_price": 0,
        })
        branch.append({
            "system": "SWAG",
            "branch": location_name,
            "brand": "Brand A",
            "category": "T-Shirts",
            "model": str(product_id),
            "on_hand": qty,
        })
        if qty < 5:
            reorder.append({
                "system": "SWAG",
                "brand": "Brand A",
                "category": "T-Shirts",
                "model": str(product_id),
                "product": product_name,
                "on_hand": qty,
                "suggest": 20,
                "priority": "Critical" if qty == 0 else "Low"
            })
    return {"total": total, "branch": branch, "reorder": reorder}

# ---------- PURCHASE ENDPOINT ----------
@app.get("/api/purchase")
def get_purchase():
    domain = [["state", "in", ["purchase", "done"]]]
    fields = ["order_id", "date_order", "partner_id", "product_id", "product_qty", "price_subtotal"]
    lines = odoo_search_read("purchase.order.line", domain, fields, limit=500)
    purchases = []
    for line in lines:
        purchases.append({
            "date": (line.get("date_order") or "")[:10],
            "po": line["order_id"][1],
            "vendor": line["partner_id"][1],
            "category": "Unknown",
            "model": line["product_id"][1],
            "qty": line["product_qty"],
            "subtotal": line["price_subtotal"],
        })
    return {"purchases": purchases}

# ---------- SALES ENDPOINT ----------
@app.get("/api/sales")
def get_sales():
    domain = [["state", "in", ["sale", "done"]]]
    fields = ["order_id", "order_partner_id", "product_id", "product_uom_qty", "price_subtotal", "create_date"]
    lines = odoo_search_read("sale.order.line", domain, fields, limit=500)
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
