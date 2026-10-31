import os, re, sqlite3, secrets, functools, smtplib
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from flask import Flask, g, request, jsonify, session, send_from_directory
from werkzeug.security import generate_password_hash as gph, check_password_hash as cph

app = Flask(__name__, static_folder="static")
app.secret_key = os.environ.get("SECRET_KEY", "dev-change-me")
DB = os.environ.get("DB_PATH", "subs.db")
STEP = {"daily": 1, "weekly": 7, "monthly": 30, "yearly": 365}
FLOW = ["draft", "quotation", "confirmed", "active", "closed"]
INV_FLOW = {"draft": ["confirmed", "cancelled"], "confirmed": ["paid", "cancelled"]}

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB); g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON"); g.db.execute("PRAGMA journal_mode=WAL")
    return g.db
@app.teardown_appcontext
def close(_):
    c = g.pop("db", None); c and c.close()
def init_db():
    c = sqlite3.connect(DB); c.executescript(open(os.path.join(os.path.dirname(__file__), "schema.sql")).read()); c.commit(); c.close()
def q(sql, a=(), one=False):
    r = db().execute(sql, a).fetchall(); r = [dict(x) for x in r]; return (r[0] if r else None) if one else r
def ex(sql, a=()):
    c = db().execute(sql, a); db().commit(); return c.lastrowid
def err(m, c=400): return jsonify(error=m), c
def today(): return date.today().isoformat()

def auth(*roles):
    def deco(f):
        @functools.wraps(f)
        def w(*a, **k):
            if "uid" not in session: return err("login required", 401)
            if roles and session["role"] not in roles: return err("forbidden", 403)
            return f(*a, **k)
        return w
    return deco

def pw_ok(p): return len(p) > 8 and re.search(r"[A-Z]", p) and re.search(r"[a-z]", p) and re.search(r"[^A-Za-z0-9]", p)

# ---------- auth ----------
@app.post("/api/signup")
def signup():
    d = request.json or {}; e = (d.get("email") or "").strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", e): return err("invalid email")
    if not pw_ok(d.get("password", "")): return err("password must be >8 chars with upper, lower and special character")
    if q("SELECT 1 FROM users WHERE email=?", (e,)): return err("email already registered", 409)
    ex("INSERT INTO users(name,email,pw,role) VALUES(?,?,?,'customer')", (d.get("name", ""), e, gph(d["password"])))
    return jsonify(ok=True), 201
@app.post("/api/login")
def login():
    d = request.json or {}; u = q("SELECT * FROM users WHERE email=?", ((d.get("email") or "").lower(),), True)
    if not u or not cph(u["pw"], d.get("password", "")): return err("invalid credentials", 401)
    session.update(uid=u["id"], role=u["role"]); return jsonify(id=u["id"], role=u["role"], name=u["name"])
@app.post("/api/logout")
def logout(): session.clear(); return jsonify(ok=True)
@app.post("/api/forgot")
def forgot():
    e = ((request.json or {}).get("email") or "").lower(); u = q("SELECT id FROM users WHERE email=?", (e,), True)
    if u:
        t = secrets.token_urlsafe(24); ex("UPDATE users SET reset_token=?,reset_exp=? WHERE id=?", (t, (datetime.utcnow() + timedelta(hours=1)).isoformat(), u["id"]))
        link = f"{request.host_url}#reset={t}"; send_mail(e, "Password reset", f"Reset your password: {link}")
    return jsonify(ok=True)  # same reply either way: no account enumeration
@app.post("/api/reset")
def reset():
    d = request.json or {}; u = q("SELECT id,reset_exp FROM users WHERE reset_token=?", (d.get("token", ""),), True)
    if not u or u["reset_exp"] < datetime.utcnow().isoformat(): return err("invalid or expired token")
    if not pw_ok(d.get("password", "")): return err("weak password")
    ex("UPDATE users SET pw=?,reset_token=NULL WHERE id=?", (gph(d["password"]), u["id"])); return jsonify(ok=True)
def send_mail(to, subj, body):
    h = os.environ.get("SMTP_HOST")
    if not h: print(f"[MAIL to {to}] {subj}: {body}"); return  # dev mode: print to console
    m = EmailMessage(); m["To"], m["From"], m["Subject"] = to, os.environ.get("SMTP_FROM", "billing@brightloop.test"), subj; m.set_content(body)
    with smtplib.SMTP(h, int(os.environ.get("SMTP_PORT", 587))) as s:
        s.starttls(); s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"]); s.send_message(m)
@app.post("/api/staff")
@auth("admin")
def add_staff():
    d = request.json or {}
    if not pw_ok(d.get("password", "")): return err("weak password")
    try: ex("INSERT INTO users(name,email,pw,role) VALUES(?,?,?,'staff')", (d.get("name"), d["email"].lower(), gph(d["password"])))
    except sqlite3.IntegrityError: return err("email already registered", 409)
    return jsonify(ok=True), 201

# ---------- generic admin CRUD (catalogue) ----------
TABLES = {"products": ["name", "type", "sales_price", "cost_price", "notes"], "variants": ["product_id", "attribute", "value", "extra_price"],
          "taxes": ["name", "rate"], "plans": ["name", "price", "period", "min_qty", "start_date", "end_date", "auto_close", "allow_pause", "allow_early_close", "allow_renew"],
          "quote_templates": ["name", "plan_id", "validity_days", "lines"],
          "discounts": ["name", "kind", "value", "min_amount", "min_qty", "start_date", "end_date", "usage_limit", "product_id", "subscription_id"]}
@app.get("/api/<t>")
@auth("admin", "staff", "customer")
def lst(t):
    if t not in TABLES: return err("not found", 404)
    return jsonify(q(f"SELECT * FROM {t} ORDER BY id DESC LIMIT 500"))
@app.post("/api/<t>")
@auth("admin")
def create(t):
    if t not in TABLES: return err("not found", 404)
    d = request.json or {}; cols = [c for c in TABLES[t] if c in d]
    if t == "plans" and d.get("period") not in STEP: return err("period must be daily/weekly/monthly/yearly")
    if t == "discounts" and d.get("kind") not in ("fixed", "percent"): return err("kind must be fixed/percent")
    try: i = ex(f"INSERT INTO {t}({','.join(cols)}) VALUES({','.join('?'*len(cols))})", [d[c] for c in cols])
    except sqlite3.Error as e: return err(str(e))
    return jsonify(id=i), 201
@app.put("/api/<t>/<int:i>")
@auth("admin")
def upd(t, i):
    if t not in TABLES: return err("not found", 404)
    d = request.json or {}; cols = [c for c in TABLES[t] if c in d]
    if cols: ex(f"UPDATE {t} SET {','.join(c+'=?' for c in cols)} WHERE id=?", [d[c] for c in cols] + [i])
    return jsonify(ok=True)
@app.delete("/api/<t>/<int:i>")
@auth("admin")
def dele(t, i):
    if t not in TABLES: return err("not found", 404)
    try: ex(f"DELETE FROM {t} WHERE id=?", (i,))
    except sqlite3.IntegrityError: return err("in use; cannot delete", 409)
    return jsonify(ok=True)

# ---------- subscriptions ----------
def next_no(prefix, table):
    return f"{prefix}{(q(f'SELECT COALESCE(MAX(id),0)+1 n FROM {table}', one=True)['n']):06d}"
@app.post("/api/subscriptions")
@auth("admin", "staff")
def new_sub():
    d = request.json or {}; plan = q("SELECT * FROM plans WHERE id=?", (d.get("plan_id"),), True)
    if not plan: return err("plan required")
    lines = d.get("lines") or []
    if not lines and d.get("template_id"):  # start from quotation template
        t = q("SELECT * FROM quote_templates WHERE id=?", (d["template_id"],), True)
        import json; lines = json.loads(t["lines"] or "[]") if t else []
    start = d.get("start_date") or today()
    sid = ex("INSERT INTO subscriptions(number,customer_id,plan_id,start_date,expiry_date,payment_terms,next_billing) VALUES(?,?,?,?,?,?,?)",
             (secrets.token_hex(4), d["customer_id"], plan["id"], start, d.get("expiry_date"), d.get("payment_terms", "Net 15"), start))
    ex("UPDATE subscriptions SET number=? WHERE id=?", (f"SUB{sid:06d}", sid))
    for l in lines:
        p = q("SELECT sales_price FROM products WHERE id=?", (l["product_id"],), True)
        v = q("SELECT extra_price FROM variants WHERE id=?", (l.get("variant_id"),), True) if l.get("variant_id") else {"extra_price": 0}
        if not p: return err("bad product")
        qty = max(int(l.get("qty", 1)), plan["min_qty"])
        ex("INSERT INTO sub_lines(subscription_id,product_id,variant_id,qty,unit_price,tax_id) VALUES(?,?,?,?,?,?)",
           (sid, l["product_id"], l.get("variant_id"), qty, p["sales_price"] + v["extra_price"], l.get("tax_id")))
    return jsonify(id=sid), 201
@app.post("/api/subscriptions/<int:i>/<action>")
@auth("admin", "staff")
def sub_action(i, action):
    s = q("SELECT s.*,p.allow_pause,p.allow_early_close,p.allow_renew FROM subscriptions s JOIN plans p ON p.id=s.plan_id WHERE s.id=?", (i,), True)
    if not s: return err("not found", 404)
    nxt = {"quote": ("draft", "quotation"), "confirm": ("quotation", "confirmed"), "activate": ("confirmed", "active")}
    if action in nxt:
        if s["status"] != nxt[action][0]: return err(f"must be {nxt[action][0]}")
        ex("UPDATE subscriptions SET status=? WHERE id=?", (nxt[action][1], i))
    elif action == "close":
        if s["status"] == "active" and not s["allow_early_close"] and not (s["expiry_date"] and s["expiry_date"] <= today()): return err("plan does not allow early close")
        ex("UPDATE subscriptions SET status='closed' WHERE id=?", (i,))
    elif action == "pause":
        if not s["allow_pause"]: return err("plan does not allow pause")
        ex("UPDATE subscriptions SET status='confirmed' WHERE id=? AND status='active'", (i,))
    elif action == "renew":
        if not s["allow_renew"]: return err("plan does not allow renew")
        ex("UPDATE subscriptions SET status='active', expiry_date=date(COALESCE(expiry_date,date('now')),'+'||?||' days') WHERE id=?", (STEP[q("SELECT period FROM plans WHERE id=?", (s["plan_id"],), True)["period"]], i))
    else: return err("unknown action")
    return jsonify(ok=True)
def scope(alias, col="customer_id"):
    return (f" AND {alias}.{col}=?", [session["uid"]]) if session["role"] == "customer" else ("", [])
@app.get("/api/subscriptions")
@auth()
def subs():
    w, a = scope("s"); st = request.args.get("status"); page = max(int(request.args.get("page", 1)), 1); n = min(int(request.args.get("size", 50)), 200)
    if st: w += " AND s.status=?"; a.append(st)
    return jsonify(q(f"SELECT s.*,u.email customer,p.name plan FROM subscriptions s JOIN users u ON u.id=s.customer_id JOIN plans p ON p.id=s.plan_id WHERE 1=1{w} ORDER BY s.id DESC LIMIT ? OFFSET ?", a + [n, (page - 1) * n]))

# ---------- billing ----------
def active_discount(sub, line_total, qty, on):
    for d in q("SELECT * FROM discounts WHERE (start_date IS NULL OR start_date<=?) AND (end_date IS NULL OR end_date>=?) AND (usage_limit IS NULL OR used<usage_limit) AND (subscription_id IS NULL OR subscription_id=?)", (on, on, sub["id"])):
        if line_total >= d["min_amount"] and qty >= d["min_qty"]:
            return d
    return None
def build_invoice(sub, on):
    """Idempotent: UNIQUE(subscription_id, period_start) guarantees nothing is billed twice."""
    if q("SELECT 1 FROM invoices WHERE subscription_id=? AND period_start=?", (sub["id"], sub["next_billing"]), True): return None
    lines = q("SELECT l.*,p.name pname,t.rate FROM sub_lines l JOIN products p ON p.id=l.product_id LEFT JOIN taxes t ON t.id=l.tax_id WHERE subscription_id=?", (sub["id"],))
    sub_total = sum(l["qty"] * l["unit_price"] for l in lines); tax = 0; disc = 0; used = set()
    for l in lines:
        amt = l["qty"] * l["unit_price"]; d = active_discount(sub, amt, l["qty"], on)
        if d and (d["product_id"] in (None, l["product_id"])):
            cut = d["value"] if d["kind"] == "fixed" else amt * d["value"] / 100; cut = min(cut, amt); disc += cut; used.add(d["id"]); amt -= cut
        tax += amt * (l["rate"] or 0) / 100
    total = round(sub_total - disc + tax, 2)
    due = (date.fromisoformat(on) + timedelta(days=int(re.sub(r"\D", "", sub["payment_terms"] or "") or 0))).isoformat()
    iid = ex("INSERT INTO invoices(number,subscription_id,customer_id,period_start,issue_date,due_date,subtotal,tax,discount,total) VALUES(?,?,?,?,?,?,?,?,?,?)",
             (secrets.token_hex(4), sub["id"], sub["customer_id"], sub["next_billing"], on, due, round(sub_total, 2), round(tax, 2), round(disc, 2), total))
    ex("UPDATE invoices SET number=? WHERE id=?", (f"INV{iid:06d}", iid))
    for l in lines: ex("INSERT INTO invoice_lines(invoice_id,description,qty,unit_price,tax_rate,amount) VALUES(?,?,?,?,?,?)", (iid, l["pname"], l["qty"], l["unit_price"], l["rate"] or 0, l["qty"] * l["unit_price"]))
    for did in used: ex("UPDATE discounts SET used=used+1 WHERE id=?", (did,))
    step = STEP[q("SELECT period FROM plans WHERE id=?", (sub["plan_id"],), True)["period"]]
    nb = (date.fromisoformat(sub["next_billing"]) + timedelta(days=step)).isoformat()
    ex("UPDATE subscriptions SET next_billing=? WHERE id=?", (nb, sub["id"]))
    return iid
@app.post("/api/billing/run")
@auth("admin", "staff")
def run_billing():
    on = (request.json or {}).get("date") or today(); made = []
    for s in q("SELECT * FROM subscriptions WHERE status='active' AND next_billing<=?", (on,)):
        if s["expiry_date"] and s["next_billing"] > s["expiry_date"]:
            ex("UPDATE subscriptions SET status='closed' WHERE id=?", (s["id"],)); continue  # auto-close at end
        i = build_invoice(s, on); i and made.append(i)
    return jsonify(created=len(made), ids=made)
@app.get("/api/invoices")
@auth()
def invoices():
    w, a = scope("i"); st = request.args.get("status"); page = max(int(request.args.get("page", 1)), 1); n = min(int(request.args.get("size", 50)), 200)
    if st == "overdue": w += " AND i.status='confirmed' AND i.due_date<?"; a.append(today())
    elif st: w += " AND i.status=?"; a.append(st)
    return jsonify(q(f"SELECT i.*,u.email customer,COALESCE((SELECT SUM(amount) FROM payments WHERE invoice_id=i.id),0) paid FROM invoices i JOIN users u ON u.id=i.customer_id WHERE 1=1{w} ORDER BY i.id DESC LIMIT ? OFFSET ?", a + [n, (page - 1) * n]))
@app.get("/api/invoices/<int:i>")
@auth()
def invoice(i):
    w, a = scope("i"); inv = q(f"SELECT i.* FROM invoices i WHERE i.id=?{w}", [i] + a, True)
    if not inv: return err("not found", 404)
    inv["lines"] = q("SELECT * FROM invoice_lines WHERE invoice_id=?", (i,)); inv["payments"] = q("SELECT * FROM payments WHERE invoice_id=?", (i,)); return jsonify(inv)
@app.post("/api/invoices/<int:i>/<action>")
@auth("admin", "staff")
def inv_action(i, action):
    inv = q("SELECT * FROM invoices WHERE id=?", (i,), True)
    if not inv: return err("not found", 404)
    target = {"confirm": "confirmed", "cancel": "cancelled"}.get(action)
    if target:
        if target not in INV_FLOW.get(inv["status"], []): return err(f"cannot {action} a {inv['status']} invoice")
        ex("UPDATE invoices SET status=? WHERE id=?", (target, i))
    elif action == "send":
        c = q("SELECT email FROM users WHERE id=?", (inv["customer_id"],), True); send_mail(c["email"], f"Invoice {inv['number']}", f"Total due: {inv['total']} by {inv['due_date']}")
    else: return err("unknown action")
    return jsonify(ok=True)
@app.post("/api/payments")
@auth("admin", "staff")
def pay():
    d = request.json or {}; inv = q("SELECT * FROM invoices WHERE id=?", (d.get("invoice_id"),), True)
    if not inv or inv["status"] != "confirmed": return err("invoice must be confirmed")
    amt = float(d.get("amount", 0))
    if amt <= 0: return err("amount must be positive")
    paid = q("SELECT COALESCE(SUM(amount),0) s FROM payments WHERE invoice_id=?", (inv["id"],), True)["s"]
    if paid + amt > inv["total"] + 0.001: return err("exceeds invoice total")
    ex("INSERT INTO payments(invoice_id,method,amount,paid_on,note) VALUES(?,?,?,?,?)", (inv["id"], d.get("method", "bank"), amt, d.get("paid_on") or today(), d.get("note")))
    if paid + amt >= inv["total"] - 0.001: ex("UPDATE invoices SET status='paid' WHERE id=?", (inv["id"],))
    return jsonify(ok=True), 201

# ---------- dashboard ----------
@app.get("/api/dashboard")
@auth("admin", "staff")
def dash():
    f, t = request.args.get("from", "0000-01-01"), request.args.get("to", "9999-12-31")
    one = lambda s, a=(): q(s, a, True)
    return jsonify(
        active_subscriptions=one("SELECT COUNT(*) n FROM subscriptions WHERE status='active'")["n"],
        revenue_collected=one("SELECT COALESCE(SUM(amount),0) n FROM payments WHERE paid_on BETWEEN ? AND ?", (f, t))["n"],
        outstanding=one("SELECT COALESCE(SUM(i.total)-(SELECT COALESCE(SUM(amount),0) FROM payments p JOIN invoices x ON x.id=p.invoice_id WHERE x.status='confirmed'),0) n FROM invoices i WHERE i.status='confirmed'")["n"],
        overdue_count=one("SELECT COUNT(*) n FROM invoices WHERE status='confirmed' AND due_date<?", (today(),))["n"],
        mrr=one("SELECT COALESCE(SUM(l.qty*l.unit_price*CASE p.period WHEN 'daily' THEN 30 WHEN 'weekly' THEN 4.345 WHEN 'yearly' THEN 1.0/12 ELSE 1 END),0) n FROM sub_lines l JOIN subscriptions s ON s.id=l.subscription_id JOIN plans p ON p.id=s.plan_id WHERE s.status='active'")["n"],
        invoices_by_status=q("SELECT status,COUNT(*) n,SUM(total) amount FROM invoices GROUP BY status"))

@app.get("/")
def index(): return send_from_directory("static", "index.html")

if __name__ == "__main__":
    init_db()
    if not q.__globals__["sqlite3"].connect(DB).execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
        c = sqlite3.connect(DB); c.execute("INSERT INTO users(name,email,pw,role) VALUES('Admin','admin@brightloop.test',?,'admin')", (gph(os.environ.get("ADMIN_PASSWORD", "Admin@12345")),)); c.commit()
    app.run(debug=os.environ.get("DEBUG") == "1", port=int(os.environ.get("PORT", 5000)))
