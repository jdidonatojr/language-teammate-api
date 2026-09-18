# api/translate-photo.py  — LANGUAGE TEAMMATE ONLY
# The phone page sends a photo (menu, sign, schedule). Claude reads any
# script and returns plain English text, one line per item.
#
# v10.7: prices are also shown in US dollars, e.g. "€24.00 (about $26)".
#        The page sends today's exchange rates (it keeps a copy on the
#        phone). If none arrive, this file fetches them itself.
#
# ENV VARS (language-teammate Vercel project):
#   ANTHROPIC_API_KEY   required
#   ALLOWED_ORIGINS     optional — comma list of sites allowed to call this,
#                       e.g. "https://www.yoursite.com,https://yoursite.com"
#                       Unset = any site may call it (fine for testing).

import json
import os
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler

MODEL = "claude-sonnet-4-6"
MAX_IMAGE_CHARS = 5_000_000   # the page shrinks photos before sending

FX_URL = "https://open.er-api.com/v6/latest/USD"
# Common travel currencies we show Claude (keeps the prompt short).
FX_CODES = ["EUR", "GBP", "JPY", "CNY", "KRW", "MXN", "CAD", "CHF", "SEK", "NOK",
            "DKK", "PLN", "CZK", "HUF", "TRY", "THB", "VND", "INR", "IDR", "PHP",
            "MYR", "SGD", "HKD", "TWD", "AUD", "NZD", "BRL", "ARS", "CLP", "COP",
            "PEN", "ZAR", "EGP", "MAD", "AED", "SAR", "ILS", "RUB", "UAH", "ISK",
            "RON", "BGN", "HRK", "RSD", "GEL", "KZT", "DOP", "CRC", "GTQ", "JMD"]


def allowed_origin(origin):
    allowed = [s.strip() for s in os.environ.get("ALLOWED_ORIGINS", "").split(",") if s.strip()]
    if not allowed:
        return origin or "*"
    return origin if origin in allowed else None


def fetch_rates():
    """Server-side fallback: today's rates, 1 USD = X. Returns (rates, date) or (None, None)."""
    try:
        with urllib.request.urlopen(FX_URL, timeout=8) as r:
            d = json.loads(r.read().decode("utf-8"))
        if d.get("result") == "success" and isinstance(d.get("rates"), dict):
            return d["rates"], (d.get("time_last_update_utc") or "")[:16]
    except Exception:
        pass
    return None, None


def rate_lines(rates):
    """Turn a rates dict into short prompt lines for the common currencies."""
    lines = []
    for code in FX_CODES:
        v = rates.get(code)
        if isinstance(v, (int, float)) and v > 0:
            lines.append(f"{code}: 1 USD = {v:.4g} {code}")
    return lines


class handler(BaseHTTPRequestHandler):

    def _send(self, status, body, origin):
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", origin or "null")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Vary", "Origin")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        origin = allowed_origin(self.headers.get("Origin", ""))
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", origin or "null")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Vary", "Origin")
        self.end_headers()

    def do_GET(self):
        origin = allowed_origin(self.headers.get("Origin", ""))
        self._send(405, {"ok": False, "error": "Use POST"}, origin)

    def do_POST(self):
        origin = allowed_origin(self.headers.get("Origin", ""))
        if origin is None:
            self._send(403, {"ok": False, "error": "This site is not allowed to use the photo translator."}, None)
            return

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            self._send(500, {"ok": False, "error": "ANTHROPIC_API_KEY is not set on the server."}, origin)
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length > 0 else b""
            data = json.loads(raw.decode("utf-8") or "{}")

            image = str(data.get("image", "") or "")
            media_type = data.get("media_type") or "image/jpeg"
            target_lang = data.get("target_lang") or "en"

            if not image:
                self._send(400, {"ok": False, "error": "No image was sent."}, origin)
                return
            if len(image) > MAX_IMAGE_CHARS:
                self._send(413, {"ok": False, "error": "Photo is too large. Please try again."}, origin)
                return

            # ---- exchange rates: from the page if sent, else fetch here ----
            rates = None
            rates_date = ""
            fx = data.get("fx")
            if isinstance(fx, dict) and isinstance(fx.get("rates"), dict):
                rates = fx["rates"]
                rates_date = str(fx.get("date") or "")
            if not rates:
                rates, rates_date = fetch_rates()
            fx_lines = rate_lines(rates) if rates else []

            lang_name = "English" if target_lang == "en" else target_lang
            instructions = (
                f"You are a travel translator. Read ALL the text in this photo, in any script or language. "
                f"Then give the {lang_name} translation only.\n"
                "Rules:\n"
                "- Keep the layout: one line per item. Keep prices, times, and numbers exactly as written.\n"
                f"- For a menu, put the dish name in {lang_name}, then a dash, then a short plain description "
                "if the dish would be unfamiliar to a traveler.\n"
                "- For a sign or schedule, translate every line in order.\n"
                "- Start with one short line saying what the photo is and its language, "
                "like \"Menu (Russian)\" or \"Train schedule (Japanese)\".\n"
                "- If part of the photo is unreadable, say \"[unreadable]\" in that spot and keep going.\n"
                "- Names of specific dishes, fish, ingredients, brands, streets, stations, and places are NEVER "
                f"translated word by word. If {lang_name} has an accepted name, use it; otherwise keep the original "
                "word exactly as written and add a very short gloss in parentheses. A wrong guess can feed someone "
                "something they can't eat or send them to the wrong street.\n"
                "- Allergens matter: if a line mentions nuts, shellfish, dairy, gluten, eggs, soy, pork, or alcohol, "
                "make sure that word survives the translation exactly.\n"
                "- If a word is unclear, put the original in square brackets after your best reading.\n"
                "- Do not add greetings, comments, or advice. Just the translation."
            )

            if fx_lines:
                instructions += (
                    "\n\nPRICES IN US DOLLARS:\n"
                    "- After every price, add the approximate US dollar amount in parentheses, "
                    "like \"€24.00 (about $26)\" or \"¥1,200 (about $8)\". Round to the nearest dollar; "
                    "under $10, round to the nearest 50 cents.\n"
                    "- Work out which currency the photo uses from its symbol, its wording, and the language. "
                    "If a plain \"$\" appears in a non-US country, use that country's dollar (Canada, Australia, etc.).\n"
                    "- Use ONLY these rates (1 USD = X):\n  " + "\n  ".join(fx_lines) + "\n"
                    "- If the currency is not in this list, or you cannot tell what it is, leave the price as written "
                    "with no dollar figure.\n"
                    "- If the photo has prices, finish with exactly one last line: "
                    "\"Dollar amounts are approximate" + (f" (rates as of {rates_date})" if rates_date else "") +
                    ". Your card may add a foreign transaction fee of about 3%.\"\n"
                    "- If the photo has no prices, do not add that line."
                )

            payload = {
                "model": MODEL,
                "max_tokens": 1800,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image}},
                        {"type": "text", "text": instructions},
                    ],
                }],
            }

            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
                method="POST",
            )

            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    out = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                try:
                    err = json.loads(e.read().decode("utf-8"))
                    msg = err.get("error", {}).get("message", f"HTTP {e.code}")
                except Exception:
                    msg = f"HTTP {e.code}"
                self._send(502, {"ok": False, "error": "Translator error: " + msg}, origin)
                return

            text = "\n".join(
                b.get("text", "") for b in out.get("content", []) if b.get("type") == "text"
            ).strip()

            if not text:
                self._send(502, {"ok": False, "error": "No text came back."}, origin)
                return

            self._send(200, {"ok": True, "text": text}, origin)

        except Exception as e:
            self._send(500, {"ok": False, "error": str(e)}, origin)
