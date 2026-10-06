from flask import Flask, request, jsonify, send_from_directory
import os
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "https://rasmm4.onrender.com"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Admin-Key"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


class PGCursor:
    def __init__(self, cursor):
        self._cursor = cursor
        self._inserted_id = None

    def execute(self, sql, params=None):
        sql = sql.replace("?", "%s")
        self._inserted_id = None

        # SQLite AUTOINCREMENT -> PostgreSQL identity column is handled in init_db.
        # Capture inserted IDs for the two existing lastrowid usages.
        stripped = sql.strip().upper()
        if stripped.startswith("INSERT INTO ORDERS") or stripped.startswith("INSERT INTO PAYMENT_REQUESTS"):
            if "RETURNING ID" not in stripped:
                sql = sql.rstrip().rstrip(";") + " RETURNING id"

        return self._cursor.execute(sql, params)

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    @property
    def lastrowid(self):
        if self._inserted_id is None:
            row = self._cursor.fetchone()
            if row:
                self._inserted_id = row["id"]
        return self._inserted_id


class PGConnection:
    def __init__(self, connection):
        self._connection = connection

    def execute(self, sql, params=None):
        cur = PGCursor(self._connection.cursor())
        cur.execute(sql, params)
        return cur

    def commit(self):
        self._connection.commit()

    def rollback(self):
        self._connection.rollback()

    def close(self):
        self._connection.close()


def db():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL ortam değişkeni bulunamadı.")
    return PGConnection(
        psycopg2.connect(database_url, cursor_factory=RealDictCursor)
    )


def init_db():
    conn = db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id TEXT PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            balance DOUBLE PRECISION DEFAULT 0
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id BIGSERIAL PRIMARY KEY,
            telegram_id TEXT,
            category TEXT,
            service TEXT,
            link TEXT,
            quantity INTEGER,
            price DOUBLE PRECISION,
            status TEXT DEFAULT 'Bekliyor',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS payment_requests (
            id BIGSERIAL PRIMARY KEY,
            telegram_id TEXT,
            amount DOUBLE PRECISION,
            txid TEXT UNIQUE,
            status TEXT DEFAULT 'Bekliyor',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

    new_balance_row = conn.execute(
        "SELECT balance FROM users WHERE telegram_id = ?",
        (telegram_id,)
    ).fetchone()
    new_balance = new_balance_row["balance"] if new_balance_row else 0

    conn.close()

    return jsonify({
        "success": True,
        "order_id": order_id,
        "status": "Bekliyor",
        "balance": new_balance
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


@app.route("/api/admin/balance/remove", methods=["POST"])
def admin_remove_balance():
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

    if user["balance"] < amount:
        conn.close()
        return jsonify({
            "success": False,
            "error": "Kullanıcının bakiyesi bu miktardan az"
        }), 400

    conn.execute(
        "UPDATE users SET balance = balance - ? WHERE telegram_id = ?",
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


# =========================
# ADMIN SİPARİŞLER
# =========================

@app.route("/api/admin/orders", methods=["GET"])
def admin_orders():
    admin_key = request.headers.get("X-Admin-Key")
    if admin_key != os.environ.get("ADMIN_KEY"):
        return jsonify({"success": False, "error": "Yetkisiz erişim"}), 403

    conn = db()
    rows = conn.execute("""
        SELECT * FROM orders
        ORDER BY id DESC
    """).fetchall()
    conn.close()

    return jsonify({
        "success": True,
        "orders": [dict(row) for row in rows]
    })


@app.route("/api/admin/order/<int:order_id>/status", methods=["POST"])
def admin_order_status(order_id):
    admin_key = request.headers.get("X-Admin-Key")
    if admin_key != os.environ.get("ADMIN_KEY"):
        return jsonify({"success": False, "error": "Yetkisiz erişim"}), 403

    data = request.get_json() or {}
    status = str(data.get("status", "")).strip()

    allowed_statuses = {"Bekliyor", "İşleniyor", "Tamamlandı", "İptal"}
    if status not in allowed_statuses:
        return jsonify({"success": False, "error": "Geçersiz sipariş durumu"}), 400

    conn = db()
    order = conn.execute("SELECT id FROM orders WHERE id = ?", (order_id,)).fetchone()

    if not order:
        conn.close()
        return jsonify({"success": False, "error": "Sipariş bulunamadı"}), 404

    conn.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
    conn.commit()
    conn.close()

    return jsonify({"success": True, "order_id": order_id, "status": status})


@app.route("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "app": "RA SMM"
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
