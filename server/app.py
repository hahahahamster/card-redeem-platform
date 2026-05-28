from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote
from email.parser import BytesParser
from email.policy import default as email_default_policy
import base64
import hashlib
import hmac
import json
import mimetypes
import os
import random
import secrets
import sqlite3
import string
import time
from datetime import datetime, timezone


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(BASE_DIR)
CLIENT_DIST_DIR = os.path.join(PROJECT_DIR, "client", "dist")
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.environ.get("CARD_DB_PATH", os.path.join(DATA_DIR, "app.db"))
SETTINGS_PATH = os.environ.get("CARD_SETTINGS_PATH", os.path.join(DATA_DIR, "settings.json"))

HOST = os.environ.get("CARD_HOST", "127.0.0.1")
PORT = int(os.environ.get("CARD_PORT", "8787"))
TOKEN_TTL_SECONDS = 24 * 60 * 60
MAX_UPLOAD_BYTES = int(os.environ.get("CARD_MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def load_settings():
    ensure_data_dir()
    if os.path.exists(SETTINGS_PATH):
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)

    settings = {"secret": secrets.token_hex(32)}
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
    return settings


SETTINGS = load_settings()


def save_settings():
    ensure_data_dir()
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(SETTINGS, f, ensure_ascii=False, indent=2)


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 120000
    ).hex()
    return f"{salt}${digest}"


def verify_password(password, stored_hash):
    try:
        salt, expected = stored_hash.split("$", 1)
    except ValueError:
        return False

    actual = hash_password(password, salt).split("$", 1)[1]
    return hmac.compare_digest(actual, expected)


def verify_admin_password(password):
    stored_hash = SETTINGS.get("admin_password_hash")
    if stored_hash:
        return verify_password(password, stored_hash)

    fallback_password = os.environ.get("CARD_ADMIN_PASSWORD", "admin123456")
    return hmac.compare_digest(password, fallback_password)


def set_admin_password(password):
    SETTINGS["admin_password_hash"] = hash_password(password)
    save_settings()


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db():
    ensure_data_dir()
    with get_db() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS products (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS inventory_items (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              product_id INTEGER NOT NULL,
              content TEXT NOT NULL,
              item_type TEXT NOT NULL DEFAULT 'text',
              filename TEXT,
              mime_type TEXT,
              file_data BLOB,
              status TEXT NOT NULL DEFAULT 'available',
              card_id INTEGER,
              created_at TEXT NOT NULL,
              delivered_at TEXT,
              FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
              FOREIGN KEY (card_id) REFERENCES cards(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS cards (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              code TEXT NOT NULL UNIQUE,
              product_id INTEGER NOT NULL,
              item_count INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL,
              used_at TEXT,
              inventory_item_id INTEGER,
              FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
              FOREIGN KEY (inventory_item_id) REFERENCES inventory_items(id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_cards_code ON cards(code);
            CREATE INDEX IF NOT EXISTS idx_cards_product_id ON cards(product_id);
            CREATE INDEX IF NOT EXISTS idx_inventory_product_status
              ON inventory_items(product_id, status);
            """
        )
        columns = {
            row["name"]
            for row in db.execute("PRAGMA table_info(cards)").fetchall()
        }
        if "item_count" not in columns:
            db.execute("ALTER TABLE cards ADD COLUMN item_count INTEGER NOT NULL DEFAULT 1")
        inventory_columns = {
            row["name"]
            for row in db.execute("PRAGMA table_info(inventory_items)").fetchall()
        }
        if "item_type" not in inventory_columns:
            db.execute("ALTER TABLE inventory_items ADD COLUMN item_type TEXT NOT NULL DEFAULT 'text'")
        if "filename" not in inventory_columns:
            db.execute("ALTER TABLE inventory_items ADD COLUMN filename TEXT")
        if "mime_type" not in inventory_columns:
            db.execute("ALTER TABLE inventory_items ADD COLUMN mime_type TEXT")
        if "file_data" not in inventory_columns:
            db.execute("ALTER TABLE inventory_items ADD COLUMN file_data BLOB")


def json_response(handler, status, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
    handler.end_headers()
    handler.wfile.write(body)


def file_response(handler, file_path):
    content_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
    with open(file_path, "rb") as f:
        body = f.read()

    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def serve_frontend(handler, path):
    if path.startswith("/api"):
        return False
    if not os.path.isdir(CLIENT_DIST_DIR):
        return False

    clean_path = unquote(path.split("?", 1)[0]).lstrip("/")
    requested = os.path.abspath(os.path.join(CLIENT_DIST_DIR, clean_path))
    dist_root = os.path.abspath(CLIENT_DIST_DIR)

    if os.path.commonpath([dist_root, requested]) != dist_root:
        json_response(handler, 403, {"ok": False, "message": "禁止访问"})
        return True

    if os.path.isdir(requested):
        requested = os.path.join(requested, "index.html")

    if os.path.isfile(requested):
        file_response(handler, requested)
        return True

    fallback = os.path.join(CLIENT_DIST_DIR, "index.html")
    if os.path.isfile(fallback):
        file_response(handler, fallback)
        return True

    return False


def read_json(handler):
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length == 0:
        return {}
    raw = handler.rfile.read(length).decode("utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError("请求体不是合法 JSON")


def read_multipart_files(handler):
    content_type = handler.headers.get("Content-Type", "")
    if "multipart/form-data" not in content_type:
        raise ValueError("请使用文件上传表单")

    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length <= 0:
        raise ValueError("请选择要上传的文件")
    if length > MAX_UPLOAD_BYTES:
        raise ValueError(f"上传内容太大，最大允许 {MAX_UPLOAD_BYTES // 1024 // 1024} MB")

    raw = handler.rfile.read(length)
    message = BytesParser(policy=email_default_policy).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
        + raw
    )

    files = []
    for part in message.iter_parts():
        filename = part.get_filename()
        if not filename:
            continue
        data = part.get_payload(decode=True) or b""
        if not data:
            continue
        files.append(
            {
                "filename": os.path.basename(filename),
                "mime_type": part.get_content_type() or "application/octet-stream",
                "data": data,
            }
        )

    if not files:
        raise ValueError("没有读取到有效文件")
    return files


def sign_token(payload):
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")
    signature = hmac.new(
        SETTINGS["secret"].encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{encoded}.{signature}"


def verify_token(token):
    try:
        encoded, signature = token.split(".", 1)
    except ValueError:
        return None

    expected = hmac.new(
        SETTINGS["secret"].encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None

    padded = encoded + "=" * (-len(encoded) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("utf-8")))
    except Exception:
        return None

    if payload.get("exp", 0) < int(time.time()):
        return None
    return payload


def require_admin(handler):
    auth = handler.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        json_response(handler, 401, {"ok": False, "message": "请先登录后台"})
        return False

    token = auth.removeprefix("Bearer ").strip()
    if not verify_token(token):
        json_response(handler, 401, {"ok": False, "message": "登录已过期，请重新登录"})
        return False
    return True


def parse_codes(text):
    normalized = text.replace(",", "\n").replace("，", "\n").replace(";", "\n")
    return [line.strip() for line in normalized.splitlines() if line.strip()]


def parse_lines(text):
    return [line.strip() for line in text.splitlines() if line.strip()]


def random_code(prefix, length):
    alphabet = string.ascii_uppercase + string.digits
    body = "".join(secrets.choice(alphabet) for _ in range(length))
    today = datetime.now().strftime("%Y%m%d")
    prefix = prefix.strip().upper() if prefix else "CARD"
    return f"{prefix}-{today}-{body}"


def rows_to_dicts(rows):
    return [dict(row) for row in rows]


def get_admin_summary():
    with get_db() as db:
        row = db.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM products) AS products,
              (SELECT COUNT(*) FROM cards) AS cards,
              (SELECT COUNT(*) FROM cards WHERE used_at IS NULL) AS unused_cards,
              (SELECT COUNT(*) FROM cards WHERE used_at IS NOT NULL) AS used_cards,
              (SELECT COUNT(*) FROM inventory_items WHERE status = 'available') AS stock
            """
        ).fetchone()
        return dict(row)


def get_products():
    with get_db() as db:
        rows = db.execute(
            """
            SELECT
              p.id,
              p.name,
              p.description,
              p.created_at,
              COUNT(DISTINCT c.id) AS cards,
              COUNT(DISTINCT CASE WHEN c.used_at IS NULL THEN c.id END) AS unused_cards,
              COUNT(DISTINCT CASE WHEN c.used_at IS NOT NULL THEN c.id END) AS used_cards,
              COUNT(DISTINCT i.id) AS total_stock,
              COUNT(DISTINCT CASE WHEN i.status = 'available' THEN i.id END) AS stock,
              COUNT(DISTINCT CASE WHEN i.status = 'delivered' THEN i.id END) AS delivered_stock
            FROM products p
            LEFT JOIN cards c ON c.product_id = p.id
            LEFT JOIN inventory_items i ON i.product_id = p.id
            GROUP BY p.id
            ORDER BY p.id DESC
            """
        ).fetchall()
        return rows_to_dicts(rows)


def get_product_detail(product_id):
    with get_db() as db:
        product = db.execute(
            """
            SELECT
              p.id,
              p.name,
              p.description,
              p.created_at,
              COUNT(DISTINCT c.id) AS cards,
              COUNT(DISTINCT CASE WHEN c.used_at IS NULL THEN c.id END) AS unused_cards,
              COUNT(DISTINCT CASE WHEN c.used_at IS NOT NULL THEN c.id END) AS used_cards,
              COUNT(DISTINCT i.id) AS total_stock,
              COUNT(DISTINCT CASE WHEN i.status = 'available' THEN i.id END) AS stock,
              COUNT(DISTINCT CASE WHEN i.status = 'delivered' THEN i.id END) AS delivered_stock
            FROM products p
            LEFT JOIN cards c ON c.product_id = p.id
            LEFT JOIN inventory_items i ON i.product_id = p.id
            WHERE p.id = ?
            GROUP BY p.id
            """,
            (product_id,),
        ).fetchone()
        if not product:
            return None

        inventory = db.execute(
            """
            SELECT
              i.id,
              i.content,
              i.item_type,
              i.filename,
              i.mime_type,
              i.status,
              i.card_id,
              i.created_at,
              i.delivered_at,
              c.code AS card_code
            FROM inventory_items i
            LEFT JOIN cards c ON c.id = i.card_id
            WHERE i.product_id = ?
            ORDER BY i.id DESC
            """,
            (product_id,),
        ).fetchall()

        cards = db.execute(
            """
            SELECT
              c.id,
              c.code,
              c.item_count,
              c.created_at,
              c.used_at,
              COUNT(i.id) AS delivered_count,
              GROUP_CONCAT(
                CASE WHEN i.item_type = 'file' THEN i.filename ELSE i.content END,
                char(10)
              ) AS delivered_content
            FROM cards c
            LEFT JOIN inventory_items i ON i.card_id = c.id
            WHERE c.product_id = ?
            GROUP BY c.id
            ORDER BY c.id DESC
            """,
            (product_id,),
        ).fetchall()

        return {
            "product": dict(product),
            "inventory": rows_to_dicts(inventory),
            "cards": rows_to_dicts(cards),
        }


def get_cards(limit=200):
    with get_db() as db:
        rows = db.execute(
            """
            SELECT
              c.id,
              c.code,
              c.item_count,
              c.created_at,
              c.used_at,
              COUNT(i.id) AS delivered_count,
              p.name AS product_name
            FROM cards c
            JOIN products p ON p.id = c.product_id
            LEFT JOIN inventory_items i ON i.card_id = c.id
            GROUP BY c.id
            ORDER BY c.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return rows_to_dicts(rows)


def redeem_one(db, code):
    card = db.execute(
        """
        SELECT c.id, c.code, c.used_at, c.product_id, c.item_count, p.name AS product_name
        FROM cards c
        JOIN products p ON p.id = c.product_id
        WHERE c.code = ?
        """,
        (code,),
    ).fetchone()

    if not card:
        return {"code": code, "status": "invalid", "message": "卡密不存在"}

    if card["used_at"]:
        return {"code": code, "status": "used", "message": "卡密已被使用"}

    item_count = max(int(card["item_count"] or 1), 1)
    items = db.execute(
        """
        SELECT id, content, item_type, filename, mime_type, file_data
        FROM inventory_items
        WHERE product_id = ? AND status = 'available'
        ORDER BY id ASC
        LIMIT ?
        """,
        (card["product_id"], item_count),
    ).fetchall()

    if len(items) < item_count:
        return {
            "code": code,
            "status": "no_stock",
            "message": f"商品库存不足，需要 {item_count} 条，当前只有 {len(items)} 条",
            "productName": card["product_name"],
        }

    timestamp = now_iso()
    item_ids = [item["id"] for item in items]
    db.executemany(
        """
        UPDATE inventory_items
        SET status = 'delivered', card_id = ?, delivered_at = ?
        WHERE id = ?
        """,
        [(card["id"], timestamp, item_id) for item_id in item_ids],
    )
    db.execute(
        """
        UPDATE cards
        SET used_at = ?, inventory_item_id = ?
        WHERE id = ?
        """,
        (timestamp, item_ids[0], card["id"]),
    )

    text_contents = [item["content"] for item in items if item["item_type"] == "text"]
    files = [
        {
            "filename": item["filename"] or item["content"] or f"file-{item['id']}",
            "mimeType": item["mime_type"] or "application/octet-stream",
            "base64": base64.b64encode(item["file_data"] or b"").decode("ascii"),
        }
        for item in items
        if item["item_type"] == "file"
    ]
    return {
        "code": code,
        "status": "success",
        "message": "提取成功",
        "productName": card["product_name"],
        "itemCount": item_count,
        "contents": text_contents,
        "content": "\n".join(text_contents),
        "files": files,
    }


def handle_redeem(handler):
    payload = read_json(handler)
    codes = parse_codes(str(payload.get("codes", "")))
    if not codes:
        json_response(handler, 400, {"ok": False, "message": "请输入卡密"})
        return

    with get_db() as db:
        db.isolation_level = None
        db.execute("BEGIN IMMEDIATE")
        try:
            results = [redeem_one(db, code) for code in codes]
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise

    json_response(handler, 200, {"ok": True, "results": results})


def handle_admin_login(handler):
    payload = read_json(handler)
    password = str(payload.get("password", ""))
    if not verify_admin_password(password):
        json_response(handler, 401, {"ok": False, "message": "后台密码不正确"})
        return

    token = sign_token({"role": "admin", "exp": int(time.time()) + TOKEN_TTL_SECONDS})
    json_response(handler, 200, {"ok": True, "token": token})


def handle_change_password(handler):
    payload = read_json(handler)
    current_password = str(payload.get("currentPassword", ""))
    new_password = str(payload.get("newPassword", ""))

    if not verify_admin_password(current_password):
        json_response(handler, 400, {"ok": False, "message": "当前密码不正确"})
        return
    if len(new_password) < 8:
        json_response(handler, 400, {"ok": False, "message": "新密码至少 8 位"})
        return
    if current_password == new_password:
        json_response(handler, 400, {"ok": False, "message": "新密码不能和当前密码一样"})
        return

    set_admin_password(new_password)
    json_response(handler, 200, {"ok": True})


def handle_create_product(handler):
    payload = read_json(handler)
    name = str(payload.get("name", "")).strip()
    description = str(payload.get("description", "")).strip()
    if not name:
        json_response(handler, 400, {"ok": False, "message": "请输入商品名称"})
        return

    with get_db() as db:
        cur = db.execute(
            "INSERT INTO products (name, description, created_at) VALUES (?, ?, ?)",
            (name, description, now_iso()),
        )
        product_id = cur.lastrowid

    json_response(handler, 200, {"ok": True, "id": product_id})


def handle_add_stock(handler, product_id):
    payload = read_json(handler)
    items = parse_lines(str(payload.get("items", "")))
    if not items:
        json_response(handler, 400, {"ok": False, "message": "请至少填写一条库存内容"})
        return

    with get_db() as db:
        product = db.execute("SELECT id FROM products WHERE id = ?", (product_id,)).fetchone()
        if not product:
            json_response(handler, 404, {"ok": False, "message": "商品不存在"})
            return
        db.executemany(
            """
            INSERT INTO inventory_items (product_id, content, status, created_at)
            VALUES (?, ?, 'available', ?)
            """,
            [(product_id, item, now_iso()) for item in items],
        )

    json_response(handler, 200, {"ok": True, "count": len(items)})


def handle_upload_stock_files(handler, product_id):
    files = read_multipart_files(handler)
    with get_db() as db:
        product = db.execute("SELECT id FROM products WHERE id = ?", (product_id,)).fetchone()
        if not product:
            json_response(handler, 404, {"ok": False, "message": "商品不存在"})
            return
        db.executemany(
            """
            INSERT INTO inventory_items (
              product_id, content, item_type, filename, mime_type, file_data, status, created_at
            )
            VALUES (?, ?, 'file', ?, ?, ?, 'available', ?)
            """,
            [
                (
                    product_id,
                    file["filename"],
                    file["filename"],
                    file["mime_type"],
                    file["data"],
                    now_iso(),
                )
                for file in files
            ],
        )

    json_response(handler, 200, {"ok": True, "count": len(files)})


def handle_generate_cards(handler):
    payload = read_json(handler)
    product_id = int(payload.get("productId") or 0)
    count = int(payload.get("count") or 0)
    item_count = int(payload.get("itemCount") or 1)
    length = int(payload.get("length") or 16)
    prefix = str(payload.get("prefix") or "CARD")

    if product_id <= 0:
        json_response(handler, 400, {"ok": False, "message": "请选择商品"})
        return
    if count <= 0 or count > 1000:
        json_response(handler, 400, {"ok": False, "message": "生成数量需在 1 到 1000 之间"})
        return
    if item_count <= 0 or item_count > 1000:
        json_response(handler, 400, {"ok": False, "message": "每张发货数量需在 1 到 1000 之间"})
        return
    if length < 8 or length > 32:
        json_response(handler, 400, {"ok": False, "message": "随机位数需在 8 到 32 之间"})
        return

    generated = []
    with get_db() as db:
        product = db.execute("SELECT id FROM products WHERE id = ?", (product_id,)).fetchone()
        if not product:
            json_response(handler, 404, {"ok": False, "message": "商品不存在"})
            return

        while len(generated) < count:
            code = random_code(prefix, length)
            try:
                db.execute(
                    """
                    INSERT INTO cards (code, product_id, item_count, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (code, product_id, item_count, now_iso()),
                )
                generated.append(code)
            except sqlite3.IntegrityError:
                continue

    json_response(handler, 200, {"ok": True, "codes": generated})


def handle_delete_product(handler, product_id):
    with get_db() as db:
        db.isolation_level = None
        db.execute("BEGIN IMMEDIATE")
        try:
            product = db.execute(
                "SELECT id FROM products WHERE id = ?", (product_id,)
            ).fetchone()
            if not product:
                db.execute("ROLLBACK")
                json_response(handler, 404, {"ok": False, "message": "商品不存在"})
                return

            db.execute("DELETE FROM inventory_items WHERE product_id = ?", (product_id,))
            db.execute("DELETE FROM cards WHERE product_id = ?", (product_id,))
            db.execute("DELETE FROM products WHERE id = ?", (product_id,))
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise

    json_response(handler, 200, {"ok": True})


def handle_delete_inventory_item(handler, item_id):
    with get_db() as db:
        db.isolation_level = None
        db.execute("BEGIN IMMEDIATE")
        try:
            item = db.execute(
                "SELECT id, product_id, card_id FROM inventory_items WHERE id = ?", (item_id,)
            ).fetchone()
            if not item:
                db.execute("ROLLBACK")
                json_response(handler, 404, {"ok": False, "message": "库存记录不存在"})
                return

            card_id = item["card_id"]
            db.execute("DELETE FROM inventory_items WHERE id = ?", (item_id,))
            if card_id:
                first_item = db.execute(
                    """
                    SELECT id
                    FROM inventory_items
                    WHERE card_id = ?
                    ORDER BY id ASC
                    LIMIT 1
                    """,
                    (card_id,),
                ).fetchone()
                db.execute(
                    "UPDATE cards SET inventory_item_id = ? WHERE id = ?",
                    (first_item["id"] if first_item else None, card_id),
                )
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise

    json_response(handler, 200, {"ok": True, "productId": item["product_id"]})


def handle_bulk_delete_inventory_items(handler):
    payload = read_json(handler)
    raw_ids = payload.get("ids", [])
    if not isinstance(raw_ids, list):
        json_response(handler, 400, {"ok": False, "message": "请选择要删除的商品"})
        return

    item_ids = sorted({int(item_id) for item_id in raw_ids if int(item_id) > 0})
    if not item_ids:
        json_response(handler, 400, {"ok": False, "message": "请选择要删除的商品"})
        return

    placeholders = ",".join("?" for _ in item_ids)
    with get_db() as db:
        db.isolation_level = None
        db.execute("BEGIN IMMEDIATE")
        try:
            rows = db.execute(
                f"""
                SELECT id, product_id, card_id
                FROM inventory_items
                WHERE id IN ({placeholders})
                """,
                item_ids,
            ).fetchall()
            if not rows:
                db.execute("ROLLBACK")
                json_response(handler, 404, {"ok": False, "message": "没有找到要删除的商品"})
                return

            product_ids = sorted({row["product_id"] for row in rows})
            card_ids = sorted({row["card_id"] for row in rows if row["card_id"]})
            db.execute(f"DELETE FROM inventory_items WHERE id IN ({placeholders})", item_ids)

            for card_id in card_ids:
                first_item = db.execute(
                    """
                    SELECT id
                    FROM inventory_items
                    WHERE card_id = ?
                    ORDER BY id ASC
                    LIMIT 1
                    """,
                    (card_id,),
                ).fetchone()
                db.execute(
                    "UPDATE cards SET inventory_item_id = ? WHERE id = ?",
                    (first_item["id"] if first_item else None, card_id),
                )

            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise

    json_response(
        handler,
        200,
        {"ok": True, "count": len(rows), "productIds": product_ids},
    )


def handle_restore_inventory_item(handler, item_id):
    with get_db() as db:
        db.isolation_level = None
        db.execute("BEGIN IMMEDIATE")
        try:
            item = db.execute(
                """
                SELECT id, product_id, card_id, status
                FROM inventory_items
                WHERE id = ?
                """,
                (item_id,),
            ).fetchone()
            if not item:
                db.execute("ROLLBACK")
                json_response(handler, 404, {"ok": False, "message": "库存记录不存在"})
                return
            if item["status"] != "delivered":
                db.execute("ROLLBACK")
                json_response(handler, 400, {"ok": False, "message": "只有已兑换内容可以恢复"})
                return

            card_id = item["card_id"]
            db.execute(
                """
                UPDATE inventory_items
                SET status = 'available', card_id = NULL, delivered_at = NULL
                WHERE id = ?
                """,
                (item_id,),
            )

            if card_id:
                remaining = db.execute(
                    "SELECT COUNT(*) AS count FROM inventory_items WHERE card_id = ?",
                    (card_id,),
                ).fetchone()["count"]
                first_item = db.execute(
                    """
                    SELECT id
                    FROM inventory_items
                    WHERE card_id = ?
                    ORDER BY id ASC
                    LIMIT 1
                    """,
                    (card_id,),
                ).fetchone()
                db.execute(
                    """
                    UPDATE cards
                    SET used_at = CASE WHEN ? = 0 THEN NULL ELSE used_at END,
                        inventory_item_id = ?
                    WHERE id = ?
                    """,
                    (remaining, first_item["id"] if first_item else None, card_id),
                )

            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise

    json_response(handler, 200, {"ok": True, "productId": item["product_id"]})


def handle_delete_card(handler, card_id):
    with get_db() as db:
        db.isolation_level = None
        db.execute("BEGIN IMMEDIATE")
        try:
            item = db.execute(
                "SELECT product_id FROM cards WHERE id = ?", (card_id,)
            ).fetchone()
            if not item:
                db.execute("ROLLBACK")
                json_response(handler, 404, {"ok": False, "message": "卡密不存在"})
                return

            db.execute("DELETE FROM inventory_items WHERE card_id = ?", (card_id,))
            db.execute("DELETE FROM cards WHERE id = ?", (card_id,))
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise

    json_response(handler, 200, {"ok": True, "productId": item["product_id"]})


def handle_restore_card(handler, card_id):
    with get_db() as db:
        db.isolation_level = None
        db.execute("BEGIN IMMEDIATE")
        try:
            card = db.execute(
                "SELECT product_id, used_at FROM cards WHERE id = ?", (card_id,)
            ).fetchone()
            if not card:
                db.execute("ROLLBACK")
                json_response(handler, 404, {"ok": False, "message": "卡密不存在"})
                return
            if not card["used_at"]:
                db.execute("ROLLBACK")
                json_response(handler, 400, {"ok": False, "message": "这张卡密还未使用"})
                return

            db.execute(
                """
                UPDATE inventory_items
                SET status = 'available', card_id = NULL, delivered_at = NULL
                WHERE card_id = ?
                """,
                (card_id,),
            )
            db.execute(
                """
                UPDATE cards
                SET used_at = NULL, inventory_item_id = NULL
                WHERE id = ?
                """,
                (card_id,),
            )
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise

    json_response(handler, 200, {"ok": True, "productId": card["product_id"]})


class AppHandler(BaseHTTPRequestHandler):
    server_version = "CardRedeem/1.0"

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args))

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.end_headers()

    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            query = parse_qs(parsed.query)

            if path == "/api/health":
                json_response(self, 200, {"ok": True})
                return

            if path == "/api/admin/summary":
                if not require_admin(self):
                    return
                json_response(self, 200, {"ok": True, "summary": get_admin_summary()})
                return

            if path == "/api/admin/products":
                if not require_admin(self):
                    return
                json_response(self, 200, {"ok": True, "products": get_products()})
                return

            parts = path.strip("/").split("/")
            if (
                len(parts) == 4
                and parts[0] == "api"
                and parts[1] == "admin"
                and parts[2] == "products"
            ):
                if not require_admin(self):
                    return
                detail = get_product_detail(int(parts[3]))
                if not detail:
                    json_response(self, 404, {"ok": False, "message": "商品不存在"})
                    return
                json_response(self, 200, {"ok": True, **detail})
                return

            if path == "/api/admin/cards":
                if not require_admin(self):
                    return
                limit = min(int(query.get("limit", ["200"])[0]), 1000)
                json_response(self, 200, {"ok": True, "cards": get_cards(limit)})
                return

            if serve_frontend(self, parsed.path):
                return

            json_response(self, 404, {"ok": False, "message": "接口不存在"})
        except Exception as exc:
            json_response(self, 500, {"ok": False, "message": str(exc)})

    def do_DELETE(self):
        try:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            parts = path.strip("/").split("/")

            if (
                len(parts) == 4
                and parts[0] == "api"
                and parts[1] == "admin"
                and parts[2] == "products"
            ):
                if not require_admin(self):
                    return
                handle_delete_product(self, int(parts[3]))
                return

            if (
                len(parts) == 4
                and parts[0] == "api"
                and parts[1] == "admin"
                and parts[2] == "inventory"
            ):
                if not require_admin(self):
                    return
                handle_delete_inventory_item(self, int(parts[3]))
                return

            if (
                len(parts) == 4
                and parts[0] == "api"
                and parts[1] == "admin"
                and parts[2] == "cards"
            ):
                if not require_admin(self):
                    return
                handle_delete_card(self, int(parts[3]))
                return

            json_response(self, 404, {"ok": False, "message": "接口不存在"})
        except ValueError as exc:
            json_response(self, 400, {"ok": False, "message": str(exc)})
        except Exception as exc:
            json_response(self, 500, {"ok": False, "message": str(exc)})

    def do_POST(self):
        try:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"

            if path == "/api/redeem":
                handle_redeem(self)
                return

            if path == "/api/admin/login":
                handle_admin_login(self)
                return

            if path == "/api/admin/password":
                if not require_admin(self):
                    return
                handle_change_password(self)
                return

            if path == "/api/admin/inventory/bulk-delete":
                if not require_admin(self):
                    return
                handle_bulk_delete_inventory_items(self)
                return

            if path == "/api/admin/products":
                if not require_admin(self):
                    return
                handle_create_product(self)
                return

            if path == "/api/admin/cards/generate":
                if not require_admin(self):
                    return
                handle_generate_cards(self)
                return

            parts = path.strip("/").split("/")
            if (
                len(parts) == 5
                and parts[0] == "api"
                and parts[1] == "admin"
                and parts[2] == "cards"
                and parts[4] == "restore"
            ):
                if not require_admin(self):
                    return
                handle_restore_card(self, int(parts[3]))
                return

            if (
                len(parts) == 5
                and parts[0] == "api"
                and parts[1] == "admin"
                and parts[2] == "inventory"
                and parts[4] == "restore"
            ):
                if not require_admin(self):
                    return
                handle_restore_inventory_item(self, int(parts[3]))
                return

            if (
                len(parts) == 5
                and parts[0] == "api"
                and parts[1] == "admin"
                and parts[2] == "products"
                and parts[4] == "files"
            ):
                if not require_admin(self):
                    return
                handle_upload_stock_files(self, int(parts[3]))
                return

            if (
                len(parts) == 5
                and parts[0] == "api"
                and parts[1] == "admin"
                and parts[2] == "products"
                and parts[4] == "stock"
            ):
                if not require_admin(self):
                    return
                handle_add_stock(self, int(parts[3]))
                return

            json_response(self, 404, {"ok": False, "message": "接口不存在"})
        except ValueError as exc:
            json_response(self, 400, {"ok": False, "message": str(exc)})
        except Exception as exc:
            json_response(self, 500, {"ok": False, "message": str(exc)})


def main():
    init_db()
    server = ThreadingHTTPServer((HOST, PORT), AppHandler)
    print(f"Card redeem API running at http://{HOST}:{PORT}")
    print("Default admin password: admin123456")
    print("You can change the admin password in the admin panel.")
    server.serve_forever()


if __name__ == "__main__":
    main()
