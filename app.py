from flask import Flask, request, jsonify, send_file
import os
import requests
from dotenv import load_dotenv

# .env dosyasını yükle
load_dotenv()
API_KEY = os.getenv("API_KEY")

app = Flask(__name__)

@app.route("/")
def index():
    # index.html dosyasını direkt gönder
    return send_file("index.html")

@app.route("/chat", methods=["POST"])
def chat():
    user_message = request.json.get("message", "")

    url = "https://api.groq.com/openai/v1/chat/completions"  # Groq endpoint
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "llama-3.1-8b-instant",
        "messages": [
            {"role": "system", "content": "Türkçe cevap ver."},
            {"role": "user", "content": user_message}
        ],
        "temperature": 0.7
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        ai_message = response.json()["choices"][0]["message"]["content"]
        return jsonify({"message": ai_message})
    except Exception as e:
        print("❌ SUNUCU HATASI ❌", e)
        return jsonify({"message": "Sunucu hatası oluştu."})

if __name__ == "__main__":
    # Render için host=0.0.0.0 ve port=10000 veya os.environ['PORT'] kullan
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=True)
