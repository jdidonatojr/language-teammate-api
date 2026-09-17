# api/translate-text.py  — LANGUAGE TEAMMATE ONLY
# Translates pasted text (a text message, an email, a note) in either
# direction. Claude detects the source language itself.
#
# Body:    { "text": "...", "target_lang": "English" }   (target is a language NAME)
# Returns: { "ok": true, "text": "...", "detected": "Italian" }
#
# ENV VARS: ANTHROPIC_API_KEY (required), ALLOWED_ORIGINS (optional, same as translate-photo)

import json
import os
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler

MODEL = "claude-sonnet-4-6"
MAX_CHARS = 4000


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
            text = str(data.get("text", "") or "").strip()
            target = str(data.get("target_lang", "") or "English").strip()[:40]
            if not text:
                self._send(400, {"ok": False, "error": "Nothing to translate."}, origin)
                return
            if len(text) > MAX_CHARS:
                text = text[:MAX_CHARS]

            instructions = (
                f"Translate the message below into {target}, the way a native speaker would write it "
                f"in a text message: natural, polite, same tone and meaning. Keep names, numbers, times, "
                f"addresses, and emojis exactly as they are.\n"
                f"Reply with ONLY a JSON object on one line: "
                f"{{\"detected\": \"<language of the original, in English>\", \"text\": \"<the translation>\"}}. "
                f"No code fences, no commentary.\n\nMESSAGE:\n{text}"
            )
            payload = {
                "model": MODEL,
                "max_tokens": 1500,
                "messages": [{"role": "user", "content": instructions}],
            }
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=json.dumps(payload).encode("utf-8"),
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
            except Exception:
                translated, detected = reply, ""
            if not translated:
                self._send(502, {"ok": False, "error": "No translation came back."}, origin)
                return
            self._send(200, {"ok": True, "text": translated, "detected": detected}, origin)
        except Exception as e:
            self._send(500, {"ok": False, "error": str(e)}, origin)
