from flask import Flask, request, jsonify, send_file
import os
import time
import requests
from collections import defaultdict, deque
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("API_KEY")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = os.getenv("MODEL", "llama-3.1-8b-instant")

app = Flask(__name__)

# ---------------------------
#  AYARLAR
# ---------------------------
MAX_HISTORY_MSGS = 14          # hafızada tutulacak toplam mesaj (user+assistant karışık)
DAILY_LIMIT = 120             # IP başına günlük mesaj limiti
REQUEST_TIMEOUT = 60

# ---------------------------
#  BELLEK / LIMIT / PROFİL
# ---------------------------
memory = defaultdict(lambda: deque(maxlen=MAX_HISTORY_MSGS))  # key -> deque([{role, content}])
daily_counter = defaultdict(lambda: {"date": "", "count": 0}) # key -> günlük sayaç
profiles = defaultdict(lambda: {"mode": "friend", "lang": "auto"})  # key -> ayarlar


MODE_HELP = {
    "friend": "Samimi, cool, doğal. Kısa soruya kısa.",
    "pro": "Daha ciddi, net, maddeli.",
    "teacher": "Adım adım, örnekli anlatır.",
    "coder": "Kod odaklı, temiz kod + kısa açıklama.",
    "roast": "Eğlenceli taşlar ama hakaret/küfür yok."
}

def get_client_id() -> str:
    # Render reverse proxy’de X-Forwarded-For gelir
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    return (request.remote_addr or "anon").strip()

def today_str() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")

def inc_daily_limit(client_id: str) -> bool:
    # True = izin var, False = limit dolu
    rec = daily_counter[client_id]
    t = today_str()
    if rec["date"] != t:
        rec["date"] = t
        rec["count"] = 0
    rec["count"] += 1
    return rec["count"] <= DAILY_LIMIT

def base_persona() -> str:
    # "Benim tarz" kişilik: saçmalamayan, cool, sözlük anlamı yapmayan
    return (
        "Senin adın 1Puzle AI. "
        "Asla LLaMA, Groq, OpenAI veya başka model/altyapı adı söyleme. "
        "Kendini her zaman 1Puzle AI olarak tanıt. "
        "Gündelik dili çok iyi anla: 'kral', 'kanka', 'reis' gibi hitapları sözlük anlamıyla açıklama. "
        "Gereksiz sözlük tanımı yapma. "
        "Emin olmadığın bilgi uydurma; gerekiyorsa 1 kısa soru sor. "
        "Kullanıcı kaba yazsa bile sen küfür/hakaret üretme. "
        "Cevaplar net, akıcı, modern olsun. "
    )

def mode_persona(mode: str) -> str:
    if mode == "pro":
        return "Profesyonel yaz. Gerektiğinde maddeler kullan. Kısa ve net ol."
    if mode == "teacher":
        return "Öğretmen gibi anlat: adım adım, örnekli, anlaşılır. Jargon az."
    if mode == "coder":
        return "Kod odaklı cevap ver. Temiz kod yaz. Kod bloklarını düzgün formatla."
    if mode == "roast":
        return "Eğlenceli taşla ama hakaret/küfür yok. Kısa, komik ve hafif."
    return "Samimi, cool ve doğal yaz. Kısa soruya kısa cevap ver."

def lang_rule(lang: str) -> str:
    if lang == "tr":
        return "Sadece Türkçe cevap ver."
    if lang == "en":
        return "Answer only in English."
    # auto:
    return (
        "Kullanıcı hangi dilde yazdıysa o dilde cevap ver. "
        "Eğer karışıksa çoğunluk dile göre cevap ver."
    )

def build_system_prompt(client_id: str) -> str:
    p = profiles[client_id]
    mode = p.get("mode", "friend")
    lang = p.get("lang", "auto")
    return base_persona() + " " + lang_rule(lang) + " " + mode_persona(mode)

def parse_command(text: str):
    t = text.strip()
    if not t.startswith("/"):
        return None, None
    parts = t.split()
    cmd = parts[0].lower()
    arg = " ".join(parts[1:]).strip() if len(parts) > 1 else ""
    return cmd, arg

def help_text() -> str:
    modes = "\n".join([f"- {k}: {v}" for k, v in MODE_HELP.items()])
    return (
        "Komutlar:\n"
        "- /help\n"
        "- /mode <friend|pro|teacher|coder|roast>\n"
        "- /lang <auto|tr|en>\n"
        "- /new  (yeni sohbet başlatır)\n"
        "- /reset (bu sohbetteki hafızayı sıfırlar)\n"
        "- /whoami\n\n"
        f"Modlar:\n{modes}\n\n"
        f"Günlük limit: {DAILY_LIMIT} mesaj/IP"
    )

def handle_command(client_id: str, cmd: str, arg: str):
    if cmd == "/help":
        return help_text()

    if cmd == "/mode":
        m = arg.lower()
        if m not in MODE_HELP:
            return "Geçersiz mod. Örnek: /mode coder"
        profiles[client_id]["mode"] = m
        return f"Tamam 😎 Mod: **{m}** — {MODE_HELP[m]}"

    if cmd == "/lang":
        l = arg.lower()
        if l not in ("auto", "tr", "en"):
            return "Geçersiz dil. Örnek: /lang auto  veya /lang tr  veya /lang en"
        profiles[client_id]["lang"] = l
        return f"Tamam ✅ Dil: **{l}**"

    if cmd == "/reset":
        memory[client_id].clear()
        return "Bu sohbetin hafızasını sıfırladım ✅"

    if cmd == "/new":
        # UI yeni sohbet açıyor zaten ama backend hafızasını da temizleyelim
        memory[client_id].clear()
        return "Yeni sohbet ✅ Yaz bakalım."

    if cmd == "/whoami":
        p = profiles[client_id]
        return f"Ayarların:\n- mode: {p.get('mode')}\n- lang: {p.get('lang')}"

    return "Bilinmeyen komut. /help yaz."

# ---------------------------
#  ROUTES
# ---------------------------
@app.get("/")
def index():
    return send_file("index.html")

@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "has_api_key": bool(API_KEY and API_KEY != "API_KEY"),
        "model": MODEL
    })

@app.post("/chat")
def chat():
    if not API_KEY or API_KEY == "API_KEY":
        return jsonify({"message": "API_KEY ayarlı değil. Render env/.env içine API_KEY ekle."}), 500

    data = request.get_json(silent=True) or {}
    user_message = (data.get("message") or "").strip()
    if not user_message:
        return jsonify({"message": "Bir mesaj yaz 😄"}), 400

    client_id = get_client_id()

    # günlük limit
    if not inc_daily_limit(client_id):
        return jsonify({"message": "Günlük limit doldu 😅 Yarın tekrar dene."}), 429

    # komutlar
    cmd, arg = parse_command(user_message)
    if cmd:
        return jsonify({"message": handle_command(client_id, cmd, arg)})

    # prompt + geçmiş
    system_prompt = build_system_prompt(client_id)

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(list(memory[client_id]))
    messages.append({"role": "user", "content": user_message})

    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.85,
        "max_tokens": 700
    }

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }

    try:
        r = requests.post(GROQ_URL, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        ai_message = r.json()["choices"][0]["message"]["content"]

        # belleğe yaz
        memory[client_id].append({"role": "user", "content": user_message})
        memory[client_id].append({"role": "assistant", "content": ai_message})

        return jsonify({"message": ai_message})

    except requests.exceptions.HTTPError:
        try:
            err = r.json()
        except Exception:
            err = {"error": r.text}
        print("❌ GROQ HTTP ERROR ❌", err)
        return jsonify({"message": "AI tarafında hata oldu. Biraz sonra tekrar dene."}), 500

    except Exception as e:
        print("❌ SERVER ERROR ❌", e)
        return jsonify({"message": "Sunucu hatası oluştu."}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=True)