from flask import Flask, request, jsonify, send_file
import os
import requests
from collections import defaultdict, deque
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("API_KEY", "API_KEY")

app = Flask(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "llama-3.1-8b-instant"

# IP bazlı basit bellek (son 12 mesaj)
memory = defaultdict(lambda: deque(maxlen=12))

SYSTEM_PROMPT = (
    "Senin adın 1Puzle AI. "
    "Asla LLaMA, Groq, OpenAI veya başka model/altyapı adı söyleme. "
    "Kendini her zaman 1Puzle AI olarak tanıt. "
    "Türkçe konuş. "
    "Kullanıcı 'naber kral', 'kanka', 'reis' gibi hitaplar kullanırsa bunu sözlük anlamıyla açıklama; "
    "gündelik konuşma olarak algıla ve doğal cevap ver. "
    "Gereksiz tanım yapma. "
    "Cevapların net, modern ve doğal olsun."
)

@app.route("/")
def index():
    return send_file("index.html")

@app.route("/chat", methods=["POST"])
def chat():
    if not API_KEY or API_KEY == "API_KEY":
        return jsonify({"message": "API_KEY ayarlı değil. Render env/.env içine API_KEY ekle."}), 500

    data = request.get_json(silent=True) or {}
    user_message = (data.get("message") or "").strip()

    if not user_message:
        return jsonify({"message": "Bir mesaj yaz 😄"}), 400

    # Render proxy varsa X-Forwarded-For gelir
    client_id = (request.headers.get("X-Forwarded-For") or request.remote_addr or "anon").split(",")[0].strip()

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(list(memory[client_id]))
    messages.append({"role": "user", "content": user_message})

    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.85,
        "max_tokens": 650
    }

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }

    try:
        r = requests.post(GROQ_URL, headers=headers, json=payload, timeout=60)
        r.raise_for_status()
        ai_message = r.json()["choices"][0]["message"]["content"]

        memory[client_id].append({"role": "user", "content": user_message})
        memory[client_id].append({"role": "assistant", "content": ai_message})

        return jsonify({"message": ai_message})

    except Exception as e:
        print("❌ SERVER ERROR ❌", e)
        return jsonify({"message": "Sunucu hatası oluştu."}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=True)