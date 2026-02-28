from flask import Flask, request, jsonify, send_file, session
import os
import uuid
import requests
from datetime import timedelta
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash

# Postgres opsiyonel import (yoksa da app çökmesin)
try:
    import psycopg2
    import psycopg2.extras
except Exception:
    psycopg2 = None

load_dotenv()

API_KEY = os.getenv("API_KEY")
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
DATABASE_URL = os.getenv("DATABASE_URL", "")

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.permanent_session_lifetime = timedelta(days=30)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "llama-3.1-8b-instant"

SYSTEM_PROMPT = (
    "Senin adın 1Puzle AI. "
    "Asla LLaMA, Groq, OpenAI veya başka model/altyapı adı söyleme. "
    "Kendini her zaman 1Puzle AI olarak tanıt. "
    "Türkçe konuş. "
    "Samimi hitapları (kral/kanka/reis) sözlük anlamıyla açıklama; gündelik konuşma gibi cevap ver. "
    "Gereksiz tanım yapma. Net ve doğal cevap ver."
)

def db_ready():
    return bool(DATABASE_URL) and (psycopg2 is not None)

def db_conn():
    # sslmode=require Render Postgres için normal
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def init_db_if_possible():
    if not db_ready():
        print("DB NOT READY (DATABASE_URL yok veya psycopg2 yok) — uygulama yine de çalışacak.")
        return

    try:
        conn = db_conn()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                pass_hash TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chats (
                id UUID PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                title TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id BIGSERIAL PRIMARY KEY,
                chat_id UUID NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                role TEXT NOT NULL CHECK (role IN ('user','assistant')),
                content TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
        """)
        conn.commit()
        cur.close()
        conn.close()
        print("DB READY ✅ tables ok")
    except Exception as e:
        print("DB INIT ERROR:", e)

init_db_if_possible()

def current_user_id():
    return session.get("user_id")

def require_login():
    if not current_user_id():
        return jsonify({"message": "Giriş yapmalısın."}), 401
    return None

@app.get("/")
def index():
    return send_file("index.html")

@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "db_ready": db_ready(),
        "has_api_key": bool(API_KEY)
    })

# ---------- AUTH ----------
@app.post("/auth/register")
def register():
    if not db_ready():
        return jsonify({"message": "Veritabanı bağlı değil. DATABASE_URL doğru değil."}), 500

    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip().lower()
    password = (data.get("password") or "").strip()

    if len(username) < 3 or len(username) > 20:
        return jsonify({"message": "Username 3-20 karakter olmalı."}), 400
    if not username.replace("_", "").isalnum():
        return jsonify({"message": "Username sadece harf/rakam ve _ içerebilir."}), 400
    if len(password) < 6:
        return jsonify({"message": "Şifre en az 6 karakter olmalı."}), 400

    pass_hash = generate_password_hash(password)

    try:
        conn = db_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("INSERT INTO users (username, pass_hash) VALUES (%s,%s) RETURNING id;",
                    (username, pass_hash))
        user_id = cur.fetchone()["id"]
        conn.commit()
        cur.close()
        conn.close()

        session.permanent = True
        session["user_id"] = user_id
        session["username"] = username
        return jsonify({"message": "Kayıt başarılı ✅", "username": username})
    except psycopg2.errors.UniqueViolation:
        return jsonify({"message": "Bu username alınmış."}), 409
    except Exception as e:
        print("REGISTER ERROR:", e)
        return jsonify({"message": "Sunucu hatası."}), 500


@app.post("/auth/login")
def login():
    if not db_ready():
        return jsonify({"message": "Veritabanı bağlı değil. DATABASE_URL doğru değil."}), 500

    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip().lower()
    password = (data.get("password") or "").strip()

    if not username or not password:
        return jsonify({"message": "Username ve şifre gerekli."}), 400

    try:
        conn = db_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("SELECT id, username, pass_hash FROM users WHERE username=%s;", (username,))
        row = cur.fetchone()
        cur.close()
        conn.close()

        if not row or not check_password_hash(row["pass_hash"], password):
            return jsonify({"message": "Hatalı username veya şifre."}), 401

        session.permanent = True
        session["user_id"] = row["id"]
        session["username"] = row["username"]
        return jsonify({"message": "Giriş başarılı ✅", "username": row["username"]})
    except Exception as e:
        print("LOGIN ERROR:", e)
        return jsonify({"message": "Sunucu hatası."}), 500


@app.post("/auth/logout")
def logout():
    session.clear()
    return jsonify({"message": "Çıkış yapıldı ✅"})


@app.get("/auth/me")
def me():
    uid = current_user_id()
    if not uid:
        return jsonify({"logged_in": False})
    return jsonify({"logged_in": True, "username": session.get("username")})


# ---------- CHAT ----------
@app.post("/chat/new")
def chat_new():
    err = require_login()
    if err: return err
    if not db_ready():
        return jsonify({"message": "Veritabanı bağlı değil. DATABASE_URL doğru değil."}), 500

    uid = current_user_id()
    chat_id = str(uuid.uuid4())

    try:
        conn = db_conn()
        cur = conn.cursor()
        cur.execute("INSERT INTO chats (id, user_id, title) VALUES (%s,%s,%s);",
                    (chat_id, uid, "Yeni Sohbet"))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"chat_id": chat_id})
    except Exception as e:
        print("CHAT_NEW ERROR:", e)
        return jsonify({"message": "Sunucu hatası."}), 500


@app.post("/chat")
def chat():
    if not API_KEY:
        return jsonify({"message": "API_KEY ayarlı değil."}), 500

    err = require_login()
    if err: return err
    if not db_ready():
        return jsonify({"message": "Veritabanı bağlı değil. DATABASE_URL doğru değil."}), 500

    data = request.get_json(silent=True) or {}
    user_message = (data.get("message") or "").strip()
    chat_id = (data.get("chat_id") or "").strip()

    if not user_message:
        return jsonify({"message": "Bir mesaj yaz 😄"}), 400
    if not chat_id:
        return jsonify({"message": "chat_id yok. Önce /chat/new çağır."}), 400

    try:
        # son 12 mesajı çek
        conn = db_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("""
            SELECT role, content
            FROM messages
            WHERE chat_id=%s
            ORDER BY id DESC
            LIMIT 12;
        """, (chat_id,))
        rows = cur.fetchall()
        rows.reverse()
        cur.close()
        conn.close()

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages += [{"role": r["role"], "content": r["content"]} for r in rows]
        messages.append({"role": "user", "content": user_message})

        payload = {
            "model": MODEL,
            "messages": messages,
            "temperature": 0.85,
            "max_tokens": 650
        }
        headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

        r = requests.post(GROQ_URL, headers=headers, json=payload, timeout=60)
        r.raise_for_status()
        ai_message = r.json()["choices"][0]["message"]["content"]

        # DB'ye yaz
        conn = db_conn()
        cur = conn.cursor()
        cur.execute("INSERT INTO messages (chat_id, role, content) VALUES (%s,%s,%s);",
                    (chat_id, "user", user_message))
        cur.execute("INSERT INTO messages (chat_id, role, content) VALUES (%s,%s,%s);",
                    (chat_id, "assistant", ai_message))
        conn.commit()
        cur.close()
        conn.close()

        return jsonify({"message": ai_message})

    except Exception as e:
        print("CHAT ERROR:", e)
        return jsonify({"message": "Sunucu hatası oluştu."}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=True)