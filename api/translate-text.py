# api/translate-text.py  — LANGUAGE TEAMMATE ONLY  (v2)
# Translates a text message in either direction, WITH context:
#   - the last few messages of the conversation (both sides)
#   - who is speaking (man/woman) and how formal to be
#   - strict rules for names of foods, dishes, fish, brands, streets, places
#
# Body: {
#   "text": "...",                       the message to translate
#   "target_lang": "Russian",            language NAME to translate into
#   "direction": "them" | "me",          "them" = I wrote it, translate for them
#                                        "me"   = they wrote it, translate for me
#   "speaker_gender": "man"|"woman"|"",  the person using the app
#   "register": "formal"|"informal"|"",  stranger vs family/friend
#   "history": [ {"who":"me"|"them", "text":"..."} , ... ]   optional, oldest first
# }
# Returns: { "ok": true, "text": "...", "detected": "Italian", "notes": "..." }

import json
import os
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler

MODEL = "claude-sonnet-4-6"
MAX_CHARS = 4000
MAX_HISTORY = 10


def allowed_origin(origin):
    allowed = [s.strip() for s in os.environ.get("ALLOWED_ORIGINS", "").split(",") if s.strip()]
    if not allowed:
        return origin or "*"
    return origin if origin in allowed else None


class handler(BaseHTTPRequestHandler):

    def _send(self, status, body, origin):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
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
            self._send(403, {"ok": False, "error": "This site is not allowed to use the translator."}, None)
            return
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            self._send(500, {"ok": False, "error": "ANTHROPIC_API_KEY is not set on the server."}, origin)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length > 0 else b""
            data = json.loads(raw.decode("utf-8") or "{}")
            text = str(data.get("text", "") or "").strip()[:MAX_CHARS]
            target = str(data.get("target_lang", "") or "English").strip()[:40]
            direction = "me" if str(data.get("direction", "")).lower() == "me" else "them"
            gender = str(data.get("speaker_gender", "") or "").lower()
            register = str(data.get("register", "") or "").lower()
            history = data.get("history") or []
            if not text:
                self._send(400, {"ok": False, "error": "Nothing to translate."}, origin)
                return

            # ---- conversation so far (context only — never re-translated)
            hist_lines = []
            for h in history[-MAX_HISTORY:]:
                if not isinstance(h, dict):
                    continue
                who = "TRAVELER" if str(h.get("who", "")) == "me" else "OTHER PERSON"
                t = str(h.get("text", "") or "").strip()[:600]
                if t:
                    hist_lines.append(f"{who}: {t}")
            hist_block = ("\n".join(hist_lines)) if hist_lines else "(none yet)"

            who_wrote = "the TRAVELER (the app user)" if direction == "them" else "the OTHER PERSON"
            gender_line = ""
            if gender in ("man", "woman"):
                gender_line = (f"The traveler is a {gender}. When the traveler is the speaker, use the "
                               f"{'masculine' if gender == 'man' else 'feminine'} forms wherever the language "
                               f"marks the speaker's gender (verbs, adjectives, endings).\n")
            register_line = ""
            if register == "formal":
                register_line = "Use the polite/formal form of address (a stranger, staff, a driver, a host).\n"
            elif register == "informal":
                register_line = "Use the familiar/informal form of address (family or a close friend).\n"

            instructions = (
                f"You are translating one text message in an ongoing conversation between a traveler and another person.\n"
                f"CONVERSATION SO FAR (for context only; do not translate it):\n{hist_block}\n\n"
                f"NEW MESSAGE, written by {who_wrote}:\n{text}\n\n"
                f"Translate the NEW MESSAGE into {target}, the way a native speaker would write it in a text "
                f"message: natural, same tone, same meaning. Use the conversation above to resolve short or "
                f"ambiguous messages (\"yes\", \"that one\", \"the same\").\n"
                f"{gender_line}{register_line}"
                "STRICT RULES:\n"
                "- Names of specific foods, dishes, fish, animals, plants, brands, products, streets, stations, "
                "and places are NEVER translated word by word. If the target language has an accepted name, use it. "
                "If you are not sure it does, keep the original word exactly as written and add a very short "
                "gloss in parentheses, e.g. \"orange roughy (a white fish)\". A wrong guess here can send someone "
                "to the wrong street or feed them something they can't eat.\n"
                "- Keep numbers, times, prices, addresses, phone numbers, and emojis exactly as they are.\n"
                "- If a word or phrase in the message is unclear or could mean two different things, do not pick "
                "silently: translate the most likely meaning and put the original in square brackets after it.\n"
                "- Do not add greetings, explanations, or anything that is not in the message.\n"
                "Reply with ONLY a JSON object on one line: "
                "{\"detected\": \"<language of the NEW MESSAGE, in English>\", \"text\": \"<the translation>\", "
                "\"notes\": \"<one short sentence ONLY if you kept a word untranslated or marked something unclear; "
                "otherwise empty>\"}. No code fences, no commentary."
            )
            payload = {"model": MODEL, "max_tokens": 1500,
                       "messages": [{"role": "user", "content": instructions}]}
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json", "x-api-key": api_key,
                         "anthropic-version": "2023-06-01"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    out = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                try:
                    msg = json.loads(e.read().decode("utf-8")).get("error", {}).get("message", f"HTTP {e.code}")
                except Exception:
                    msg = f"HTTP {e.code}"
                self._send(502, {"ok": False, "error": "Translator error: " + msg}, origin)
                return

            reply = "\n".join(b.get("text", "") for b in out.get("content", []) if b.get("type") == "text").strip()
            reply = reply.strip("`").strip()
            if reply.lower().startswith("json"):
                reply = reply[4:].strip()
            try:
                parsed = json.loads(reply)
                translated = str(parsed.get("text", "")).strip()
                detected = str(parsed.get("detected", "")).strip()
                notes = str(parsed.get("notes", "") or "").strip()
            except Exception:
                translated, detected, notes = reply, "", ""
            if not translated:
                self._send(502, {"ok": False, "error": "No translation came back."}, origin)
                return
            self._send(200, {"ok": True, "text": translated, "detected": detected, "notes": notes}, origin)
        except Exception as e:
            self._send(500, {"ok": False, "error": str(e)}, origin)
