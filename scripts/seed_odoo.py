"""Fill Odoo with test customers and products, then create one sales order.

Run it as many times as you like: it never creates duplicates.

    python scripts/seed_odoo.py
"""

import os
import sys
import xmlrpc.client

from dotenv import load_dotenv

load_dotenv()

ODOO_URL = os.environ.get("ODOO_URL", "http://localhost:8069")
ODOO_DB = os.environ.get("ODOO_DB", "orderdesk")
ODOO_USER = os.environ.get("ODOO_USER", "admin")
ODOO_API_KEY = os.environ.get("ODOO_API_KEY", "")

CUSTOMERS = [
    {"name": "BTP Martin", "email": "karim@btp-martin.fr", "city": "Villeurbanne"},
    {"name": "Garage Dubois", "email": "achats@garage-dubois.fr", "city": "Lyon"},
    {"name": "Menuiserie Leroy", "email": "contact@menuiserie-leroy.fr", "city": "Grenoble"},
]

PRODUCTS = [
    {"default_code": "VIS-HEX-M8-40-ZN", "name": "Hex bolt M8x40 zinc plated", "list_price": 0.12},
    {"default_code": "VIS-HEX-M8-60-ZN", "name": "Hex bolt M8x60 zinc plated", "list_price": 0.16},
    {"default_code": "VIS-HEX-M10-50-ZN", "name": "Hex bolt M10x50 zinc plated", "list_price": 0.22},
    {"default_code": "RON-M8-PLATE", "name": "Flat washer M8 zinc plated", "list_price": 0.03},
    {"default_code": "RON-M8-GROWER", "name": "Spring washer M8 zinc plated", "list_price": 0.05},
    {"default_code": "ECR-M8-ZN", "name": "Hex nut M8 zinc plated", "list_price": 0.04},
    {"default_code": "ECR-M10-ZN", "name": "Hex nut M10 zinc plated", "list_price": 0.06},
    {"default_code": "CHE-M8-80-BOX", "name": "Concrete anchor M8x80, box of 50", "list_price": 18.50},
    {"default_code": "CHE-M10-100-BOX", "name": "Concrete anchor M10x100, box of 25", "list_price": 21.90},
    {"default_code": "VIS-BOIS-5-50-BOX", "name": "Wood screw 5x50 torx, box of 200", "list_price": 9.80},
]

# The example order from Karim (PO BM-2291)
TEST_ORDER = {
    "customer": "BTP Martin",
    "po_number": "BM-2291",
    "lines": [
        ("VIS-HEX-M8-40-ZN", 500),
        ("RON-M8-PLATE", 200),
        ("CHE-M8-80-BOX", 10),
    ],
}


class Odoo:
    """Tiny helper around Odoo's XML-RPC API."""

    def __init__(self) -> None:
        if not ODOO_API_KEY or ODOO_API_KEY == "paste-your-api-key-here":
            sys.exit("ODOO_API_KEY is missing. Create one in Odoo and put it in .env (README step 5).")

        common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
        self.uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_API_KEY, {})
        if not self.uid:
            sys.exit("Login failed. Check ODOO_DB, ODOO_USER and ODOO_API_KEY in .env.")
        self.models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")

    def call(self, model: str, method: str, *args, **kwargs):
        return self.models.execute_kw(ODOO_DB, self.uid, ODOO_API_KEY, model, method, list(args), kwargs)

    def find_one(self, model: str, domain: list) -> int | None:
        ids = self.call(model, "search", domain, limit=1)
        return ids[0] if ids else None


def seed_customers(odoo: Odoo) -> dict[str, int]:
    ids = {}
    for c in CUSTOMERS:
        existing = odoo.find_one("res.partner", [["name", "=", c["name"]]])
        if existing:
            ids[c["name"]] = existing
            print(f"  customer exists : {c['name']}")
        else:
            ids[c["name"]] = odoo.call("res.partner", "create", {**c, "is_company": True})
            print(f"  customer created: {c['name']}")
    return ids


def seed_products(odoo: Odoo) -> dict[str, int]:
    ids = {}
    for p in PRODUCTS:
        existing = odoo.find_one("product.product", [["default_code", "=", p["default_code"]]])
        if existing:
            ids[p["default_code"]] = existing
            print(f"  product exists  : {p['default_code']}")
        else:
            ids[p["default_code"]] = odoo.call(
                "product.product", "create", {**p, "type": "consu", "sale_ok": True}
            )
            print(f"  product created : {p['default_code']}")
    return ids


def create_order(odoo: Odoo, customers: dict[str, int], products: dict[str, int]) -> int:
    partner_id = customers[TEST_ORDER["customer"]]
    po = TEST_ORDER["po_number"]

    # Idempotency: if an order with this customer reference already exists, don't create another.
    existing = odoo.find_one(
        "sale.order", [["partner_id", "=", partner_id], ["client_order_ref", "=", po]]
    )
    if existing:
        print(f"  order exists    : PO {po} -> sale.order id {existing}")
        return existing

    lines = [
        [0, 0, {"product_id": products[code], "product_uom_qty": qty}]
        for code, qty in TEST_ORDER["lines"]
    ]
    order_id = odoo.call(
        "sale.order",
        "create",
        {"partner_id": partner_id, "client_order_ref": po, "order_line": lines},
    )
    print(f"  order created   : PO {po} -> sale.order id {order_id}")
    return order_id


def main() -> None:
    print(f"Connecting to {ODOO_URL} (database: {ODOO_DB})")
    odoo = Odoo()
    print("Customers:")
    customers = seed_customers(odoo)
    print("Products:")
    products = seed_products(odoo)
    print("Order:")
    order_id = create_order(odoo, customers, products)

    order = odoo.call("sale.order", "read", [order_id], fields=["name", "state", "amount_total"])[0]
    print(f"\nDone. Odoo order {order['name']} | state: {order['state']} | total: {order['amount_total']}")
    print("Open http://localhost:8069 -> Sales -> Orders to see it.")


if __name__ == "__main__":
    main()
