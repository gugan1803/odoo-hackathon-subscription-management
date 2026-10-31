import os, tempfile
os.environ["DB_PATH"] = tempfile.mktemp(suffix=".db")
import app as A
A.init_db()
c = A.app.test_client()
import sqlite3; from werkzeug.security import generate_password_hash as g
s = sqlite3.connect(A.DB); s.execute("INSERT INTO users(name,email,pw,role) VALUES('A','a@x.io',?,'admin')", (g("Admin@12345"),)); s.commit()
def J(m, u, **k): r = getattr(c, m)(u, **k); return r.status_code, r.get_json()
def test_flow():
    assert J("post", "/api/signup", json={"email": "c@x.io", "password": "weak"})[0] == 400
    assert J("post", "/api/signup", json={"email": "c@x.io", "password": "Strong@Pass1"})[0] == 201
    assert J("post", "/api/signup", json={"email": "c@x.io", "password": "Strong@Pass1"})[0] == 409
    J("post", "/api/login", json={"email": "a@x.io", "password": "Admin@12345"})
    pid = J("post", "/api/products", json={"name": "ERP", "sales_price": 1000})[1]["id"]
    vid = J("post", "/api/variants", json={"product_id": pid, "attribute": "brand", "value": "Odoo", "extra_price": 560})[1]["id"]
    tid = J("post", "/api/taxes", json={"name": "GST", "rate": 18})[1]["id"]
    plid = J("post", "/api/plans", json={"name": "Monthly", "price": 0, "period": "monthly", "start_date": "2026-01-01"})[1]["id"]
    J("post", "/api/discounts", json={"name": "10%", "kind": "percent", "value": 10, "usage_limit": 1})
    cid = 2
    sid = J("post", "/api/subscriptions", json={"customer_id": cid, "plan_id": plid, "start_date": "2026-10-01", "lines": [{"product_id": pid, "variant_id": vid, "qty": 2, "tax_id": tid}]})[1]["id"]
    for a in ("quote", "confirm", "activate"): assert J("post", f"/api/subscriptions/{sid}/{a}")[0] == 200
    assert J("post", f"/api/subscriptions/{sid}/confirm")[0] == 400
    r = J("post", "/api/billing/run", json={"date": "2026-10-01"})[1]; assert r["created"] == 1
    assert J("post", "/api/billing/run", json={"date": "2026-10-01"})[1]["created"] == 0  # never twice
    inv = J("get", f"/api/invoices/{r['ids'][0]}")[1]
    assert inv["subtotal"] == 3120 and inv["discount"] == 312 and inv["total"] == round(2808 * 1.18, 2)
    J("post", f"/api/invoices/{inv['id']}/confirm")
    assert J("post", "/api/payments", json={"invoice_id": inv["id"], "amount": inv["total"], "method": "upi"})[0] == 201
    assert J("get", "/api/invoices?status=paid")[1][0]["id"] == inv["id"]
    assert J("get", "/api/dashboard")[1]["revenue_collected"] == inv["total"]
