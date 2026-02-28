from flask import Flask, request, jsonify, send_file
import os
import requests
from collections import defaultdict, deque
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("API_KEY")

app = Flask(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "llama-3.1-8b-instant"

# Kullanıcı başına bellek (son 16 mesaj)
memory = defaultdict(lambda: deque(maxlen=16))

# Kullanıcı profili (geçici: sunucu restart atınca sıfırlanır)
profiles = defaultdict(lambda: {
    "name": None,
    "mode": "friend",   # friend | pro | teacher | coder | roast | therapist
    "lang": "auto"      # auto | tr | en | de | fr | es | ar | ...
})

# Mod açıklamaları (UI’ya dokunmadan /help ile görünecek)
MODE_HELP = {
    "friend": "Samimi, cool, kısa ve doğal.",
    "pro": "Daha ciddi, net, maddeli ve profesyonel.",
    "teacher": "Öğretmen modu: adım adım anlatır, örnek verir.",
    "coder": "Kod odaklı: kısa açıklama + temiz kod.",
    "roast": "Eğlenceli taşlar ama hakaret/küfür yok.",
    "therapist": "Destekleyici, sakin, yargılamaz (tıbbi teşhis yok).",
}

def client_id():
    # Render reverse proxy: X-Forwarded-For gelebilir
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or "anon"

def base_persona():
    # "Benim kişiliğim" tarzı: net, cool, gereksiz sözlük yapmayan
    return (
        "Senin adın 1Puzle AI. "
        "Asla LLaMA, Groq, OpenAI veya başka model/altyapı adı söyleme. "
        "Kendini her zaman 1Puzle AI olarak tanıt. "
        "Gündelik dili çok iyi anla: 'kral', 'kanka', 'reis' gibi hitapları sözlük anlamıyla açıklama. "
        "Gereksiz tanım ve gereksiz uzatma yapma. "
        "Saçmalama: emin olmadığın şeyi uydurma; gerekiyorsa 1 kısa soru sor. "
        "Kullanıcı küfür etse bile sen küfür etme. "
        "Cevapların doğal, modern, net olsun. "
    )

def mode_persona(mode: str):
    # Modlara göre ekstra davranış
    if mode == "pro":
        return "Daha profesyonel yaz. Gerektiğinde maddelerle. Kısa ve net."
    if mode == "teacher":
        return "Öğretmen gibi: adım adım, örnekli, anlaşılır. Gereksiz jargon yok."
    if mode == "coder":
        return "Kod odaklı yaz. Temiz kod ver. Kod bloklarını düzgün formatla. Kısa açıklama ekle."
    if mode == "roast":
        return "Eğlenceli taşla ama aşağılamadan, hakaret/küfür olmadan. Kısa, komik."
    if mode == "therapist":
        return "Destekleyici ve sakin yaz. Yargılama. Tıbbi/psikiyatrik teşhis koyma."
    # friend default
    return "Samimi, cool ve doğal yaz. Kısa soruya kısa, uzun soruya düzenli cevap ver."

def lang_rule(lang: str):
    if lang == "tr":
        return "Sadece Türkçe cevap ver."
    if lang == "en":
        return "Answer only in English."
    if lang == "auto":
        return (
            "Kullanıcı hangi dilde yazdıysa o dilde cevap ver. "
            "Eğer karışıksa, çoğunluk dile göre cevap ver."
        )
    # diğer diller için genel kural
    return f"Kullanıcı '{lang}' dilinde yazarsa o dilde cevap ver; değilse kullanıcının dilini takip et."

def system_prompt_for(user_profile: dict):
    name = user_profile.get("name")
    mode = user_profile.get("mode", "friend")
    lang = user_profile.get("lang", "auto")

    identity = base_persona()
    identity += "Kullanıcı mesajı basitse basit cevap ver; teknikse teknik cevap ver. "

    if name:
        identity += f"Kullanıcının adı {name}. Uygun yerlerde ismiyle hitap edebilirsin (abartma). "

    identity += "Komutlar: /help yazarsa komutları açıkla. "
    identity += lang_rule(lang) + " "
    identity += mode_persona(mode)

    return identity

def parse_command(text: str):
    # Basit komut parser
    t = text.strip()
    if not t.startswith("/"):
        return None, None

    parts = t.split()
    cmd = parts[0].lower()
    arg = " ".join(parts[1:]).strip() if len(parts) > 1 else ""

    return cmd, arg

def handle_command(cid: str, cmd: str, arg: str):
    p = profiles[cid]

    if cmd in ("/help", "/komutlar"):
        modes_list = "\n".join([f"- {k}: {v}" for k, v in MODE_HELP.items()])
        return (
            "Komutlar:\n"
            "- /mode <friend|pro|teacher|coder|roast|therapist>\n"
            "- /lang <auto|tr|en>\n"
            "- /name <isim>\n"
            "- /reset (sohbet hafızasını sıfırlar)\n"
            "- /whoami (ayarlarını gösterir)\n\n"
            f"Modlar:\n{modes_list}"
        )

    if cmd == "/mode":
        m = arg.lower()
        if m not in MODE_HELP:
            return "Geçersiz mod. Örnek: /mode coder"
        p["mode"] = m
        return f"Tamam 😎 Mod: **{m}** ({MODE_HELP[m]})"

    if cmd == "/lang":
        l = arg.lower()
        if l not in ("auto", "tr", "en"):
            return "Geçersiz dil. Örnek: /lang auto  veya  /lang tr  veya  /lang en"
        p["lang"] = l
        return f"Tamam ✅ Dil: **{l}**"

    if cmd == "/name":
        if not arg:
            return "İsim ver. Örnek: /name Yusuf"
        p["name"] = arg[:32]
        return f"Tamam ✅ Kaydettim: **{p['name']}**"

    if cmd == "/reset":
        memory[cid].clear()
        return "Sohbet hafızasını sıfırladım ✅"

    if cmd in ("/whoami", "/me"):
        return f"Ayarların:\n- name: {p['name']}\n- mode: {p['mode']}\n- lang: {p['lang']}"

    return "Bilinmeyen komut. /help yaz."

@app.route("/")
def index():
    return send_file("index.html")

@app.route("/chat", methods=["POST"])
def chat():
    if not API_KEY or API_KEY == "API_KEY":
        return jsonify({"message": "API_KEY ayarlı değil. Render/ .env içine API_KEY ekle."}), 500

    data = request.get_json(silent=True) or {}
    user_message = (data.get("message") or "").strip()
    if not user_message:
        return jsonify({"message": "Bir mesaj yaz 😄"}), 400

    cid = client_id()

    # Komutlar
    cmd, arg = parse_command(user_message)
    if cmd:
        reply = handle_command(cid, cmd, arg)
        return jsonify({"message": reply})

    # Bellek + sistem prompt
    profile = profiles[cid]
    system = system_prompt_for(profile)

    history = list(memory[cid])
    messages = [{"role": "system", "content": system}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.85,
        "max_tokens": 650
    }

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        r = requests.post(GROQ_URL, headers=headers, json=payload, timeout=60)
        r.raise_for_status()
        j = r.json()
        ai_message = j["choices"][0]["message"]["content"]

        # Belleğe yaz
        memory[cid].append({"role": "user", "content": user_message})
        memory[cid].append({"role": "assistant", "content": ai_message})

        return jsonify({"message": ai_message})

    except Exception as e:
        print("❌ SERVER ERROR ❌", e)
        return jsonify({"message": "Sunucu hatası oluştu."}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=True)