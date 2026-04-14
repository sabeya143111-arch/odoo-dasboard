from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import xmlrpc.client  # Odoo XML-RPC [web:115]

# ---------- ODOO CONFIG ----------
ODOO_URL = "http://YOUR_ODOO_HOST:8069"
ODOO_DB = "YOUR_DB_NAME"
ODOO_USER = "your_user@example.com"
ODOO_PASSWORD = "YOUR_PASSWORD_OR_API_KEY"

common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_PASSWORD, {})
if not uid:
    raise Exception("Failed to authenticate to Odoo – check credentials")

models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")

# ---------- FASTAPI APP ----------
app = FastAPI()

# CORS so that your HTML (localhost / any domain) can call this API [web:114][web:117]
origins = [
    "http://localhost",
    "http://127.0.0.1",
    "http://localhost:5500",   # VSCode Live Server
    "http://localhost:8000",   # etc...
    "*"                        # dev only, tighten later
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- HELPERS TO CALL ODOO ----------

def odoo_search_read(model, domain, fields, limit=100):
    return models.execute_kw(
        ODOO_DB, uid, ODOO_PASSWORD,
        model, "search_read",
        [domain],
        {"fields": fields, "limit": limit}
    )

# ---------- SCHEMAS ----------

class StockResponse(BaseModel):
    total: list
    branch: list
    reorder: list

class PurchaseResponse(BaseModel):
    purchases: list

class SalesResponse(BaseModel):
    sales: list

# ---------- STOCK ENDPOINT ----------
@app.get("/api/stock", response_model=StockResponse)
def get_stock():
    # Example using stock.quant for SWAG company / locations
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

        # You will map these to your brand/category model (custom fields or related) later
        total.append({
            "system": "SWAG",
            "brand": "Brand A",      # placeholder mapping
            "category": "T-Shirts",  # placeholder
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

        # simple reorder rule
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

    return {
        "total": total,
        "branch": branch,
        "reorder": reorder,
    }

# ---------- PURCHASE ENDPOINT ----------
@app.get("/api/purchase", response_model=PurchaseResponse)
def get_purchase():
    # Purchase order lines (confirmed / done)
    domain = [["state", "in", ["purchase", "done"]]]
    fields = ["order_id", "date_order", "partner_id", "product_id", "product_qty", "price_subtotal"]
    lines = odoo_search_read("purchase.order.line", domain, fields, limit=500)

    purchases = []
    for line in lines:
        po_name = line["order_id"][1]
        date = line.get("date_order") or ""
        vendor = line["partner_id"][1]
        product = line["product_id"][1]
        qty = line["product_qty"]
        subtotal = line["price_subtotal"]

        purchases.append({
            "date": date[:10],
            "po": po_name,
            "vendor": vendor,
            "category": "Unknown",  # map from product category if you want
            "model": product,
            "qty": qty,
            "subtotal": subtotal,
        })
    return {"purchases": purchases}

# ---------- SALES ENDPOINT ----------
@app.get("/api/sales", response_model=SalesResponse)
def get_sales():
    # Sales order lines (confirmed / done)
    domain = [["state", "in", ["sale", "done"]]]
    fields = ["order_id", "order_partner_id", "product_id", "product_uom_qty", "price_subtotal", "create_date"]
    lines = odoo_search_read("sale.order.line", domain, fields, limit=500)

    sales = []
    for line in lines:
      so_name = line["order_id"][1]
      customer = line["order_partner_id"][1]
      product = line["product_id"][1]
      qty = line["product_uom_qty"]
      subtotal = line["price_subtotal"]
      date = (line.get("create_date") or "")[:10]

      sales.append({
          "date": date,
          "so": so_name,
          "customer": customer,
          "model": product,
          "qty": qty,
          "subtotal": subtotal,
      })
    return {"sales": sales}
