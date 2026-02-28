from flask import Flask, request, jsonify, send_file
import os
import requests
from collections import defaultdict, deque
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("API_KEY")

app = Flask(__name__)

# Basit hafıza (kullanıcı başına son 10 mesaj)
memory = defaultdict(lambda: deque(maxlen=10))

SYSTEM_PROMPT = (
    "Senin adın 1Puzle AI. "
    "Asla LLaMA, Groq, OpenAI veya başka model adı söyleme. "
    "Kendini her zaman 1Puzle AI olarak tanıt. "
    "Türkçe konuş. "
    "Kullanıcı samimi konuşursa samimi cevap ver. "
    "'kral', 'kanka', 'reis' gibi hitapları sözlük anlamıyla açıklama. "
    "Gereksiz tanım yapma. "
    "Cevapların net, modern ve doğal olsun."
)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

@app.route("/")
def index():
    return send_file("index.html")

@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json()
    user_message = data.get("message", "").strip()

    if not user_message:
        return jsonify({"message": "Bir şey yaz 😄"}), 400

    if API_KEY == "API_KEY":
        return jsonify({"message": "API_KEY ayarlı değil."}), 500

    client_id = request.remote_addr
    history = list(memory[client_id])

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    payload = {
        "model": "llama-3.1-8b-instant",
        "messages": messages,
        "temperature": 0.9,
        "max_tokens": 600
    }

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(GROQ_URL, headers=headers, json=payload)
        response.raise_for_status()
        ai_message = response.json()["choices"][0]["message"]["content"]

        # hafızaya ekle
        memory[client_id].append({"role": "user", "content": user_message})
        memory[client_id].append({"role": "assistant", "content": ai_message})

        return jsonify({"message": ai_message})

    except Exception as e:
        print("HATA:", e)
        return jsonify({"message": "Sunucu hatası oluştu."}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)