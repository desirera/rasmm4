from flask import Flask, request, jsonify, send_from_directory
import sqlite3
import os

app = Flask(__name__)


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "https://rasmm4.onrender.com"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Admin-Key"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response

DB = "rasmm.db"


def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id TEXT PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            balance REAL DEFAULT 0
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id TEXT,
            category TEXT,
            service TEXT,
            link TEXT,
            quantity INTEGER,
            price REAL,
            status TEXT DEFAULT 'Bekliyor',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS payment_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id TEXT,
            amount REAL,
            txid TEXT,
            status TEXT DEFAULT 'Bekliyor',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


init_db()


@app.route("/")
def home():
    return send_from_directory(".", "index.html")


@app.route("/api/user", methods=["POST"])
def create_user():
    data = request.get_json() or {}

    telegram_id = str(data.get("telegram_id", ""))
    username = data.get("username", "")
    first_name = data.get("first_name", "")

    if not telegram_id:
        return jsonify({"error": "telegram_id gerekli"}), 400

    conn = db()

    existing = conn.execute(
        "SELECT * FROM users WHERE telegram_id = ?",
        (telegram_id,)
    ).fetchone()

    if existing:
        conn.execute("""
            UPDATE users
            SET username = ?, first_name = ?
            WHERE telegram_id = ?
        """, (username, first_name, telegram_id))
    else:
        conn.execute("""
            INSERT INTO users
            (telegram_id, username, first_name, balance)
            VALUES (?, ?, ?, 0)
        """, (telegram_id, username, first_name))

    conn.commit()
    conn.close()

    return jsonify({
        "success": True,
        "telegram_id": telegram_id
    })


@app.route("/api/balance/<telegram_id>", methods=["GET"])
def get_balance(telegram_id):
    conn = db()

    user = conn.execute(
        "SELECT balance FROM users WHERE telegram_id = ?",
        (telegram_id,)
    ).fetchone()

    conn.close()

    if not user:
        return jsonify({"balance": 0})

    return jsonify({
        "balance": user["balance"]
    })


@app.route("/api/order", methods=["POST"])
def create_order():
    data = request.get_json() or {}

    telegram_id = str(data.get("telegram_id", ""))
    category = data.get("category", "")
    service = data.get("service", "")
    link = data.get("link", "")
    quantity = int(data.get("quantity", 0))
    price = float(data.get("price", 0))

    if not telegram_id or not service or not link or quantity <= 0:
        return jsonify({
            "success": False,
            "error": "Eksik sipariş bilgisi"
        }), 400

    conn = db()

    user = conn.execute(
        "SELECT balance FROM users WHERE telegram_id = ?",
        (telegram_id,)
    ).fetchone()

    if not user:
        conn.close()
        return jsonify({
            "success": False,
            "error": "Kullanıcı bulunamadı"
        }), 400

    if user["balance"] < price:
        conn.close()
        return jsonify({
            "success": False,
            "error": "Yetersiz bakiye"
        }), 400

    conn.execute("""
        UPDATE users
        SET balance = balance - ?
        WHERE telegram_id = ?
    """, (price, telegram_id))

    cursor = conn.execute("""
        INSERT INTO orders
        (telegram_id, category, service, link, quantity, price, status)
        VALUES (?, ?, ?, ?, ?, ?, 'Bekliyor')
    """, (
        telegram_id,
        category,
        service,
        link,
        quantity,
        price
    ))

    order_id = cursor.lastrowid

    conn.commit()
    conn.close()

    return jsonify({
        "success": True,
        "order_id": order_id,
        "status": "Bekliyor"
    })


@app.route("/api/orders/<telegram_id>", methods=["GET"])
def get_orders(telegram_id):
    conn = db()

    orders = conn.execute("""
        SELECT *
        FROM orders
        WHERE telegram_id = ?
        ORDER BY id DESC
    """, (telegram_id,)).fetchall()

    conn.close()

    result = []

    for order in orders:
        result.append({
            "id": order["id"],
            "category": order["category"],
            "service": order["service"],
            "link": order["link"],
            "quantity": order["quantity"],
            "price": order["price"],
            "status": order["status"],
            "created_at": order["created_at"]
        })

    return jsonify(result)


@app.route("/api/payment", methods=["POST"])
def create_payment_request():
    data = request.get_json() or {}

    telegram_id = str(data.get("telegram_id", ""))
    amount = float(data.get("amount", 0))
    txid = str(data.get("txid", "")).strip()

    if not telegram_id or amount <= 0 or not txid:
        return jsonify({
            "success": False,
            "error": "Tutar ve TXID gerekli"
        }), 400

    conn = db()

    user = conn.execute(
        "SELECT telegram_id FROM users WHERE telegram_id = ?",
        (telegram_id,)
    ).fetchone()

    if not user:
        conn.close()
        return jsonify({
            "success": False,
            "error": "Kullanıcı bulunamadı"
        }), 404

    pending = conn.execute(
        "SELECT id FROM payment_requests WHERE txid = ?",
        (txid,)
    ).fetchone()

    if pending:
        conn.close()
        return jsonify({
            "success": False,
            "error": "Bu TXID daha önce bildirildi"
        }), 409

    cursor = conn.execute("""
        INSERT INTO payment_requests
        (telegram_id, amount, txid, status)
        VALUES (?, ?, ?, 'Bekliyor')
    """, (telegram_id, amount, txid))

    request_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return jsonify({
        "success": True,
        "request_id": request_id,
        "status": "Bekliyor"
    })


@app.route("/api/admin/payments", methods=["GET"])
def admin_payments():
    admin_key = request.headers.get("X-Admin-Key")
    if admin_key != os.environ.get("ADMIN_KEY"):
        return jsonify({"success": False, "error": "Yetkisiz erişim"}), 403

    conn = db()
    rows = conn.execute("""
        SELECT * FROM payment_requests
        ORDER BY id DESC
    """).fetchall()
    conn.close()

    return jsonify({
        "success": True,
        "payments": [dict(row) for row in rows]
    })


@app.route("/api/admin/payment/<int:request_id>/approve", methods=["POST"])
def approve_payment(request_id):
    admin_key = request.headers.get("X-Admin-Key")
    if admin_key != os.environ.get("ADMIN_KEY"):
        return jsonify({"success": False, "error": "Yetkisiz erişim"}), 403

    conn = db()
    row = conn.execute(
        "SELECT * FROM payment_requests WHERE id = ?",
        (request_id,)
    ).fetchone()

    if not row:
        conn.close()
        return jsonify({"success": False, "error": "Ödeme bildirimi bulunamadı"}), 404

    if row["status"] != "Bekliyor":
        conn.close()
        return jsonify({"success": False, "error": "Bu bildirim zaten işlendi"}), 409

    user = conn.execute(
        "SELECT balance FROM users WHERE telegram_id = ?",
        (row["telegram_id"],)
    ).fetchone()

    if not user:
        conn.close()
        return jsonify({"success": False, "error": "Kullanıcı bulunamadı"}), 404

    conn.execute(
        "UPDATE users SET balance = balance + ? WHERE telegram_id = ?",
        (row["amount"], row["telegram_id"])
    )
    conn.execute(
        "UPDATE payment_requests SET status = 'Onaylandı' WHERE id = ?",
        (request_id,)
    )
    conn.commit()

    new_balance = conn.execute(
        "SELECT balance FROM users WHERE telegram_id = ?",
        (row["telegram_id"],)
    ).fetchone()["balance"]
    conn.close()

    return jsonify({
        "success": True,
        "balance": new_balance
    })


@app.route("/api/admin/payment/<int:request_id>/reject", methods=["POST"])
def reject_payment(request_id):
    admin_key = request.headers.get("X-Admin-Key")
    if admin_key != os.environ.get("ADMIN_KEY"):
        return jsonify({"success": False, "error": "Yetkisiz erişim"}), 403

    conn = db()
    row = conn.execute(
        "SELECT status FROM payment_requests WHERE id = ?",
        (request_id,)
    ).fetchone()

    if not row:
        conn.close()
        return jsonify({"success": False, "error": "Ödeme bildirimi bulunamadı"}), 404

    if row["status"] != "Bekliyor":
        conn.close()
        return jsonify({"success": False, "error": "Bu bildirim zaten işlendi"}), 409

    conn.execute(
        "UPDATE payment_requests SET status = 'Reddedildi' WHERE id = ?",
        (request_id,)
    )
    conn.commit()
    conn.close()

    return jsonify({"success": True})


@app.route("/api/admin/balance", methods=["POST"])
def admin_balance():
    data = request.get_json() or {}

    admin_key = request.headers.get("X-Admin-Key")
    if admin_key != os.environ.get("ADMIN_KEY"):
        return jsonify({
            "success": False,
            "error": "Yetkisiz erişim"
        }), 403

    telegram_id = str(data.get("telegram_id", ""))
    amount = float(data.get("amount", 0))

    if not telegram_id or amount <= 0:
        return jsonify({
            "success": False,
            "error": "Geçersiz bilgiler"
        }), 400

    conn = db()

    user = conn.execute(
        "SELECT balance FROM users WHERE telegram_id = ?",
        (telegram_id,)
    ).fetchone()

    if not user:
        conn.close()
        return jsonify({
            "success": False,
            "error": "Kullanıcı bulunamadı"
        }), 404

    conn.execute(
        "UPDATE users SET balance = balance + ? WHERE telegram_id = ?",
        (amount, telegram_id)
    )

    conn.commit()

    new_balance = conn.execute(
        "SELECT balance FROM users WHERE telegram_id = ?",
        (telegram_id,)
    ).fetchone()["balance"]

    conn.close()

    return jsonify({
        "success": True,
        "balance": new_balance
    })


@app.route("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "app": "RA SMM"
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
