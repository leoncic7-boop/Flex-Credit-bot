"""
Bot Viber pentru FlexCredit Moldova (O.C.N. „FLEX FINANCIAL” S.R.L.) — clienți cu rate/restanțe.

Funcționează fără biblioteci externe (doar Python 3.8+).
- Răspunde în română sau rusă, după limba clientului.
- Răspunde la întrebări frecvente: cum plătesc, plată în rate, contestare,
  de ce am fost contactat, program, date de contact.
- NU afișează sume sau date personale în chat. Pentru întrebări despre
  datorie, colectează o cerere de apel (nume, nr. contract, telefon) și
  o salvează în fișierul cereri_apel.csv.
- Cu ANTHROPIC_API_KEY: botul DISCUTĂ liber cu clienții prin AI (Claude), ține minte
  conversația, și înregistrează singur cererile de apel. Reguli stricte: politicos,
  fără amenințări, fără sume. Fără cheie: răspunsuri fixe după cuvinte-cheie.

Variabile de mediu:
  VIBER_TOKEN        - tokenul botului de la Viber (obligatoriu)
  WEBHOOK_URL        - adresa publică HTTPS (pe Render nu e nevoie, se ia automat)
  PORT               - portul serverului (implicit 8080)
  ANTHROPIC_API_KEY  - cheia AI (console.anthropic.com) — pentru discuție liberă
  FISIER_CERERI      - opțional, unde se salvează cererile (implicit cereri_apel.csv)
"""

import csv
import datetime
import hashlib
import hmac
import json
import os
import re
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ======================================================================
# 1. SETĂRI — completați datele companiei aici
# ======================================================================

NUME_BOT = "FlexCredit"
AVATAR_URL = ""  # opțional: link către logo

COMPANIE = "FlexCredit (O.C.N. „FLEX FINANCIAL” S.R.L.)"
TELEFON = "14447"
EMAIL = "office@flexcredit.md"
ADRESA = "mun. Chișinău, str. Alba-Iulia nr. 75"
PROGRAM_RO = "luni–duminică, 09:00–18:00"
PROGRAM_RU = "понедельник–воскресенье, 09:00–18:00"
SITE_PLATA = "https://www.flexcredit.md/plata-cu-cardul"
ORASE_RO = "Chișinău, Bălți, Soroca, Briceni, Ungheni, Florești și Comrat"
ORASE_RU = "Кишинёв, Бельцы, Сорока, Бричаны, Унгены, Флорешты и Комрат"

# Fiecare intrare: cuvinte-cheie (fără diacritice) -> (răspuns RO, răspuns RU)
# sau -> "APEL" ca botul să înceapă cererea de apel.
# Ordinea contează: prima potrivire câștigă.
RASPUNSURI = [
    (
        ("operator", "sunati-ma", "sunati ma", "apel telefonic", "consultant", "om ", "оператор", "позвоните", "перезвоните", "консультант"),
        "APEL",
    ),
    (
        ("datoria mea", "cat datorez", "suma", "sold", "cat am de plata", "restanta",
         "сколько я должен", "сумма", "мой долг", "задолженность"),
        "APEL",
    ),
    (
        ("rate", "esalon", "nu pot plati", "nu am bani", "amanare", "restructur", "intelegere",
         "рассрочк", "не могу платить", "нет денег", "отсрочк", "договорит"),
        (
            "Înțelegem că pot apărea dificultăți. Putem discuta o plată în rate sau un grafic "
            "adaptat posibilităților dumneavoastră. Scrieți „operator” și vă contactează un specialist.",
            "Мы понимаем, что бывают трудности. Можно обсудить оплату в рассрочку или удобный график. "
            "Напишите «оператор», и с вами свяжется специалист.",
        ),
    ),
    (
        ("cum platesc", "plata", "platesc", "achit", "iban", "rechizite", "terminal",
         "как оплатить", "оплат", "реквизит", "платеж"),
        (
            "Puteți achita ratele:\n"
            f"• online cu cardul, gratuit: {SITE_PLATA}\n"
            f"• la orice oficiu FlexCredit ({ORASE_RO})\n"
            "• la orice oficiu poștal din țară\n"
            "• prin transfer bancar, în contul indicat în contractul de credit.",
            "Оплатить взносы можно:\n"
            f"• онлайн картой, бесплатно: {SITE_PLATA}\n"
            f"• в любом офисе FlexCredit ({ORASE_RU})\n"
            "• в любом почтовом отделении страны\n"
            "• банковским переводом на счёт, указанный в кредитном договоре.",
        ),
    ),
    (
        ("nu recunosc", "contest", "nu e datoria", "nu am luat", "gresit", "am achitat", "deja platit",
         "не признаю", "не мой долг", "не брал", "ошибка", "уже оплатил", "оспор"),
        (
            "Aveți dreptul să contestați datoria. Trimiteți-ne o cerere scrisă cu explicațiile "
            f"și documentele dumneavoastră (ex. dovada plății) pe {EMAIL} sau la sediu, {ADRESA}. "
            "Vom verifica și vă vom răspunde.",
            "Вы вправе оспорить долг. Отправьте письменное заявление с пояснениями и документами "
            f"(например, подтверждение оплаты) на {EMAIL} или в офис: {ADRESA}. "
            "Мы проверим и ответим вам.",
        ),
    ),
    (
        ("de ce", "cine sunteti", "ce companie", "почему", "кто вы"),
        (
            f"Suntem {COMPANIE}, organizație de creditare nebancară înregistrată la CNPF. "
            "Vă contactăm în legătură cu creditul dumneavoastră la FlexCredit. "
            "Detaliile vi le poate oferi un operator după verificarea identității. Scrieți „operator”.",
            f"Мы — {COMPANIE}, небанковская кредитная организация, зарегистрированная в НКФР. "
            "Мы связываемся с вами по поводу вашего кредита в FlexCredit. "
            "Подробности сообщит оператор после проверки личности. Напишите «оператор».",
        ),
    ),
    (
        ("program", "orar", "deschis", "график", "часы работы", "режим"),
        (f"Programul nostru: {PROGRAM_RO}.", f"Наш график работы: {PROGRAM_RU}."),
    ),
    (
        ("adresa", "unde", "sediu", "oficiu", "birou", "telefon", "contact", "email", "адрес", "где", "офис", "телефон", "контакт"),
        (
            f"{COMPANIE}\nSediul: {ADRESA}\nOficii: {ORASE_RO}\nTelefon: {TELEFON}\nEmail: {EMAIL}\nProgram: {PROGRAM_RO}",
            f"{COMPANIE}\nГлавный офис: {ADRESA}\nОфисы: {ORASE_RU}\nТелефон: {TELEFON}\nEmail: {EMAIL}\nГрафик: {PROGRAM_RU}",
        ),
    ),
    (
        ("buna", "salut", "hello", "здравствуйте", "привет", "добрый"),
        ("Bună ziua! Cu ce vă pot ajuta?", "Здравствуйте! Чем могу помочь?"),
    ),
]

MESAJ_BUN_VENIT = (
    f"Bună ziua! Sunteți în chatul {COMPANIE}. Alegeți o opțiune de mai jos sau scrieți întrebarea.\n\n"
    f"Здравствуйте! Это чат {COMPANIE}. Выберите вариант ниже или напишите вопрос."
)

MESAJ_NECUNOSCUT = (
    "Nu am înțeles exact întrebarea. Alegeți o opțiune de mai jos sau scrieți „operator”.",
    "Я не совсем понял вопрос. Выберите вариант ниже или напишите «оператор».",
)

# Butoane: (text afișat, text trimis la apăsare)
BUTOANE = [
    ("Datoria mea", "datoria mea"),
    ("Cum plătesc", "cum platesc"),
    ("Plată în rate", "plata in rate"),
    ("Contestare", "contest"),
    ("Contacte", "contact"),
    ("Operator", "operator"),
]

# Ce știe AI-ul despre firmă. Completați aici orice informație nouă: botul o va folosi în discuții.
INFO_AFACERE = f"""
Companie: {COMPANIE}, organizație de creditare nebancară din Republica Moldova,
înregistrată în registrul CNPF sub nr. 193 din 07.12.2022. Sediul: {ADRESA}.
Telefon gratuit/național: {TELEFON}. Email: {EMAIL}. Program: {PROGRAM_RO}.
Oficii în: {ORASE_RO}.

Cum se plătesc ratele:
- online cu cardul, gratuit: {SITE_PLATA} (clientul introduce IDNP-ul și vede suma de plată);
- la orice oficiu FlexCredit;
- la orice oficiu poștal din țară;
- prin transfer bancar, în contul indicat în contractul de credit.

Dacă clientul nu poate plăti: se poate discuta cu un specialist o eșalonare sau un grafic adaptat.
Contestare: clientul trimite o cerere scrisă cu explicații și documente la {EMAIL} sau la sediu.
Produse: „Flex în Rate” (1 300 – 89 000 lei, plată lunară) și „Flex până la Salariu” (plată unică).
"""

# ======================================================================
# 2. LOGICA BOTULUI — de obicei nu trebuie modificată
# ======================================================================

VIBER_API = "https://chatapi.viber.com/pa"
TOKEN = os.environ.get("VIBER_TOKEN", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
FISIER_CERERI = os.environ.get("FISIER_CERERI", "cereri_apel.csv")

DIACRITICE = str.maketrans("ăâîșşțţĂÂÎȘŞȚŢ", "aaisstt" + "AAISSTT")
CHIRILIC = re.compile(r"[а-яА-ЯёЁ]")

# Conversațiile în curs pentru cererea de apel: id_user -> {"pas":..., "limba":..., date...}
STARI = {}
LACAT = threading.Lock()

PASI_APEL = {
    "nume": (
        "Pentru siguranța datelor dumneavoastră, nu oferim informații despre credit în chat. "
        f"Suma de plată o puteți vedea introducând IDNP-ul aici: {SITE_PLATA}\n\n"
        "Sau vă sună un operator. Scrieți, vă rog, numele și prenumele (sau „anulare” pentru a renunța):",
        "В целях безопасности мы не сообщаем данные о кредите в чате. "
        f"Сумму к оплате можно узнать, введя IDNP здесь: {SITE_PLATA}\n\n"
        "Или вам перезвонит оператор. Напишите, пожалуйста, имя и фамилию (или «отмена», чтобы отказаться):",
    ),
    "contract": (
        "Mulțumesc! Numărul contractului sau IDNP-ul (dacă nu îl știți, scrieți „nu știu”):",
        "Спасибо! Номер договора или IDNP (если не знаете, напишите «не знаю»):",
    ),
    "telefon": (
        "Numărul de telefon la care vă putem suna:",
        "Номер телефона, по которому вам можно позвонить:",
    ),
    "gata": (
        f"Cererea a fost înregistrată. Un operator vă va suna în programul de lucru ({PROGRAM_RO}). Mulțumim!",
        f"Заявка зарегистрирована. Оператор позвонит вам в рабочее время ({PROGRAM_RU}). Спасибо!",
    ),
    "telefon_gresit": (
        "Numărul pare incorect. Scrieți-l, vă rog, din nou (ex. 069123456 sau +37369123456):",
        "Номер выглядит неверно. Напишите его, пожалуйста, ещё раз (например, 069123456 или +37369123456):",
    ),
    "anulat": ("Cererea a fost anulată.", "Заявка отменена."),
}


def normalizeaza(text):
    return text.translate(DIACRITICE).lower()


def limba(text):
    return 1 if CHIRILIC.search(text) else 0  # 0 = RO, 1 = RU


def tastatura():
    return {
        "Type": "keyboard",
        "DefaultHeight": False,
        "Buttons": [
            {"Columns": 3, "Rows": 1, "ActionType": "reply", "ActionBody": trimis,
             "Text": afisat, "TextSize": "regular"}
            for afisat, trimis in BUTOANE
        ],
    }


def expeditor():
    s = {"name": NUME_BOT}
    if AVATAR_URL:
        s["avatar"] = AVATAR_URL
    return s


def cerere_viber(metoda, date):
    req = urllib.request.Request(
        f"{VIBER_API}/{metoda}",
        data=json.dumps(date).encode("utf-8"),
        headers={"X-Viber-Auth-Token": TOKEN, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        rezultat = json.loads(r.read().decode("utf-8"))
    if rezultat.get("status") != 0:
        print(f"[Viber] Eroare la {metoda}: {rezultat}", file=sys.stderr)
    return rezultat


def trimite_text(destinatar, text):
    return cerere_viber("send_message", {
        "receiver": destinatar,
        "min_api_version": 1,
        "sender": expeditor(),
        "type": "text",
        "text": text,
        "keyboard": tastatura(),
    })


REGULI_AI = f"""Ești asistentul virtual FlexCredit Moldova pe Viber și discuți cu clienții firmei,
mai ales cu cei care au rate de plătit sau restanțe.

Cum vorbești:
- Răspunzi în limba în care scrie clientul (română sau rusă), pe un ton calm, politicos și uman.
- Mesaje scurte, potrivite pentru telefon: 1–4 propoziții. Fără markdown, fără titluri.
- Ții minte ce a spus clientul mai devreme în conversație.
- Arăți înțelegere când clientul are greutăți și cauți împreună cu el o soluție (plata online,
  eșalonare, apel de la un specialist).

Reguli stricte:
- Nu amenința, nu presa, nu vorbi despre instanță, executori, penalități sau consecințe.
- Nu cunoști datele clientului: nu spune sume, termene, nr. de contract sau alte detalii despre creditul lui.
  Pentru suma de plată trimite linkul {SITE_PLATA} (se introduce IDNP-ul) sau propune un apel.
- Nu promite reduceri, anulări de datorie sau aprobări. Spune doar că un specialist poate discuta.
- Nu da sfaturi juridice. Nu inventa informații care nu sunt mai jos.
- Dacă clientul e nepoliticos, rămâi calm și politicos.

Cerere de apel:
Dacă clientul vrea să vorbească cu un om, întreabă de suma lui, vrea eșalonare sau are o problemă pe care
nu o poți rezolva, propune-i un apel de la un specialist. Cere pe rând: numele și prenumele, numărul
contractului sau IDNP-ul (poate spune că nu știe) și numărul de telefon. Când le ai pe toate trei,
confirmă-i că îl sună un specialist în programul de lucru și adaugă LA SFÂRȘITUL mesajului, pe un rând separat:
[CERERE_APEL]nume|contract sau IDNP|telefon|motivul pe scurt[/CERERE_APEL]
Clientul nu vede acest rând. Folosește-l o singură dată pentru fiecare cerere.

Informații despre firmă:
{INFO_AFACERE}"""

MODEL_AI = os.environ.get("MODEL_AI", "claude-haiku-5-5")
ISTORIC = {}            # id_user -> {"t": ultima activitate, "mesaje": [...]}
ISTORIC_MAX = 30        # câte mesaje păstrăm pe conversație
ISTORIC_EXPIRA = 24 * 3600  # după 24h fără mesaje, conversația începe de la zero
MARCAJ = re.compile(r"\[CERERE_APEL\](.*?)\[/CERERE_APEL\]", re.S)


def cheama_claude(mesaje):
    corp = {"model": MODEL_AI, "max_tokens": 500, "system": REGULI_AI, "messages": mesaje}
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(corp).encode("utf-8"),
        headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=40) as r:
        date = json.loads(r.read().decode("utf-8"))
    return "".join(b.get("text", "") for b in date.get("content", [])).strip()


def conversatie_ai(id_user, nume_viber, text):
    """Discuție cu AI, cu memorie. Returnează None dacă AI-ul nu e disponibil."""
    if not ANTHROPIC_KEY:
        return None
    import time
    with LACAT:
        conv = ISTORIC.get(id_user)
        if not conv or time.time() - conv["t"] > ISTORIC_EXPIRA:
            conv = ISTORIC[id_user] = {"t": time.time(), "mesaje": []}
        conv["t"] = time.time()
        conv["mesaje"].append({"role": "user", "content": text[:2000]})
        conv["mesaje"] = conv["mesaje"][-ISTORIC_MAX:]
        while conv["mesaje"] and conv["mesaje"][0]["role"] != "user":
            conv["mesaje"].pop(0)
        mesaje = list(conv["mesaje"])
    try:
        raspuns = cheama_claude(mesaje)
    except Exception as e:
        print(f"[AI] Eroare: {e}", file=sys.stderr, flush=True)
        with LACAT:
            conv["mesaje"].pop()  # scoatem mesajul fără răspuns
        return None
    if not raspuns:
        return None

    # Cererea de apel: o salvăm și o ascundem de client
    for potrivire in MARCAJ.finditer(raspuns):
        campuri = [c.strip() for c in potrivire.group(1).split("|")] + ["", "", "", ""]
        salveaza_cerere(id_user, nume_viber, {
            "nume": campuri[0], "contract": campuri[1], "telefon": campuri[2],
            "motiv": campuri[3], "limba": limba(text),
        })
    vizibil = MARCAJ.sub("", raspuns).strip()

    with LACAT:
        conv["mesaje"].append({"role": "assistant", "content": raspuns})
    return vizibil or None


def salveaza_cerere(id_user, nume_viber, stare):
    nou = not os.path.exists(FISIER_CERERI)
    with LACAT, open(FISIER_CERERI, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if nou:
            w.writerow(["data", "nume", "contract_idnp", "telefon", "motiv", "nume_viber", "viber_id", "limba"])
        w.writerow([
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            stare["nume"], stare["contract"], stare["telefon"], stare.get("motiv", ""),
            nume_viber, id_user, "RU" if stare["limba"] else "RO",
        ])


def continua_apel(id_user, nume_viber, text):
    """Pasul următor din cererea de apel."""
    stare = STARI[id_user]
    L = stare["limba"]
    t = normalizeaza(text).strip()
    if t in ("anulare", "anuleaza", "stop", "отмена", "стоп"):
        del STARI[id_user]
        return PASI_APEL["anulat"][L]

    if stare["pas"] == "nume":
        stare["nume"], stare["pas"] = text.strip()[:100], "contract"
        return PASI_APEL["contract"][L]
    if stare["pas"] == "contract":
        stare["contract"], stare["pas"] = text.strip()[:50], "telefon"
        return PASI_APEL["telefon"][L]
    if stare["pas"] == "telefon":
        cifre = re.sub(r"[^\d+]", "", text)
        if len(re.sub(r"\D", "", cifre)) < 8:
            return PASI_APEL["telefon_gresit"][L]
        stare["telefon"] = cifre
        salveaza_cerere(id_user, nume_viber, stare)
        del STARI[id_user]
        return PASI_APEL["gata"][L]


def alege_raspuns(id_user, nume_viber, text):
    # Cu AI: discuție liberă. Dacă AI-ul nu e configurat sau dă eroare, răspunsuri fixe.
    if id_user not in STARI:
        r = conversatie_ai(id_user, nume_viber, text)
        if r:
            return r
    return raspuns_fix(id_user, nume_viber, text)


def raspuns_fix(id_user, nume_viber, text):
    if id_user in STARI:
        return continua_apel(id_user, nume_viber, text)

    L = limba(text)
    t = normalizeaza(text) + " "
    for cuvinte, raspuns in RASPUNSURI:
        if any(c in t for c in cuvinte):
            if raspuns == "APEL":
                STARI[id_user] = {"pas": "nume", "limba": L}
                return PASI_APEL["nume"][L]
            return raspuns[L]
    return MESAJ_NECUNOSCUT[L]


def semnatura_valida(corp, semnatura):
    asteptat = hmac.new(TOKEN.encode(), corp, hashlib.sha256).hexdigest()
    return hmac.compare_digest(asteptat, semnatura or "")


class Handler(BaseHTTPRequestHandler):
    def _raspunde(self, cod=200, corp=None):
        date = json.dumps(corp or {}, ensure_ascii=False).encode("utf-8")
        self.send_response(cod)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(date)))
        self.end_headers()
        self.wfile.write(date)

    def do_GET(self):
        self._raspunde(200, {"status": "botul funcționează", "legat_de_viber": bool(TOKEN)})

    def do_POST(self):
        corp = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if not TOKEN or not semnatura_valida(corp, self.headers.get("X-Viber-Content-Signature")):
            return self._raspunde(403, {"error": "semnătură invalidă"})

        ev = json.loads(corp.decode("utf-8"))
        tip = ev.get("event")

        if tip == "conversation_started":
            return self._raspunde(200, {
                "sender": expeditor(), "type": "text",
                "text": MESAJ_BUN_VENIT, "keyboard": tastatura(),
            })

        if tip == "message":
            mesaj, sender = ev.get("message", {}), ev.get("sender", {})
            self._raspunde(200)
            id_user = sender.get("id")
            if mesaj.get("type") == "text":
                trimite_text(id_user, alege_raspuns(id_user, sender.get("name", ""), mesaj.get("text", "")))
            else:
                trimite_text(id_user, "Am primit fișierul, mulțumim. Pentru documente scrieți pe "
                                      f"{EMAIL}. / Документы, пожалуйста, отправьте на {EMAIL}.")
            return

        self._raspunde(200)

    def log_message(self, fmt, *args):
        print("[HTTP]", fmt % args)


def seteaza_webhook(url):
    return cerere_viber("set_webhook", {
        "url": url,
        "event_types": ["delivered", "seen", "failed", "subscribed", "unsubscribed", "conversation_started"],
        "send_name": True,
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Botul ascultă pe portul {port}", flush=True)

    # Render completează singur RENDER_EXTERNAL_URL; altfel se folosește WEBHOOK_URL.
    adresa = os.environ.get("WEBHOOK_URL") or os.environ.get("RENDER_EXTERNAL_URL")

    if not TOKEN:
        print("ATENȚIE: lipsește VIBER_TOKEN. Serverul merge, dar nu e legat de Viber.", flush=True)
    elif not adresa:
        print("ATENȚIE: lipsește WEBHOOK_URL. Adăugați adresa publică HTTPS a serverului.", flush=True)
    else:
        def leaga():
            import time
            time.sleep(3)  # așteptăm să pornească serverul
            try:
                print("Webhook Viber:", seteaza_webhook(adresa), flush=True)
            except Exception as e:
                print("Eroare la legarea cu Viber:", e, flush=True)
        threading.Thread(target=leaga, daemon=True).start()

    server.serve_forever()
