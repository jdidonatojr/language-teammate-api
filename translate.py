"""
Language Teammate — /api/translate  (cloned from ESource Course Developer)  (POST)
(Property of Westfield Enterprises — part of the LumaQuest project.)

Translates the text lines of one slide for the course player's
language layer. Called cross-origin from the Yola player pages, so it
answers CORS preflights and validates input strictly (it is public).

Body:    { "target_lang": "es", "lines": ["line one", "line two", ...] }
Returns: { "lines": ["línea uno", "línea dos", ...] }  (same order/length)
"""

import os
import re
import json
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler

CLAUDE_MODEL = 'claude-haiku-4-5-20251001'
MAX_LINES = 80
MAX_CHARS = 400

LANGS = {
    'es': 'Spanish', 'fr': 'French', 'de': 'German', 'pt': 'Portuguese',
    'it': 'Italian', 'ja': 'Japanese', 'zh': 'Simplified Chinese',
    'ko': 'Korean', 'hi': 'Hindi', 'ar': 'Arabic', 'nl': 'Dutch',
    'pl': 'Polish', 'tr': 'Turkish', 'vi': 'Vietnamese', 'ru': 'Russian',
}

PROMPT = """Translate each line of slide text below into {language}.

Rules:
- Return ONLY a JSON array of strings: the translations, in the SAME
  order, with the SAME number of items as the input. No commentary,
  no code fences.
- Keep product names, company names, brand names,
  URLs, email addresses, and numbers exactly as they are.
- Keep translations concise — this is slide text, not prose.
- If a line is only a URL, number, or name, return it unchanged.

Input lines (JSON):
{lines_json}"""


def cors_headers(handler):
    handler.send_header('Access-Control-Allow-Origin', '*')
    handler.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
    handler.send_header('Access-Control-Allow-Headers', 'Content-Type')


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        cors_headers(self)
        self.end_headers()

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            payload = json.loads(self.rfile.read(length).decode('utf-8')) if length else {}

            target = str(payload.get('target_lang', '')).strip().lower()
            lines = payload.get('lines')

            if target not in LANGS:
                return self._send_error(400, 'Unsupported language.')
            if not isinstance(lines, list) or not lines or len(lines) > MAX_LINES:
                return self._send_error(400, 'lines must be a non-empty list (max %d).' % MAX_LINES)
            clean = []
            for l in lines:
                s = str(l)[:MAX_CHARS]
                clean.append(s)

            api_key = os.environ.get('ANTHROPIC_API_KEY', '').strip()
            if not api_key:
                return self._send_error(500, 'ANTHROPIC_API_KEY not configured.')

            prompt = PROMPT.format(language=LANGS[target],
                                   lines_json=json.dumps(clean, ensure_ascii=False))
            body = {
                'model': CLAUDE_MODEL,
                'max_tokens': 4096,
                'messages': [{'role': 'user', 'content': prompt}]
            }
            req = urllib.request.Request(
                'https://api.anthropic.com/v1/messages',
                data=json.dumps(body).encode('utf-8'),
                headers={'x-api-key': api_key, 'anthropic-version': '2023-06-01',
                         'content-type': 'application/json'},
                method='POST')
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode('utf-8'))

            text = ''
            for block in result.get('content', []):
                if block.get('type') == 'text':
                    text = block['text'].strip()
                    break
            text = re.sub(r'^```[a-zA-Z]*\s*', '', text)
            text = re.sub(r'\s*```$', '', text).strip()
            translated = json.loads(text)
            if not isinstance(translated, list) or len(translated) != len(clean):
                return self._send_error(500, 'Translation came back malformed — try again.')

            out = json.dumps({'lines': [str(t) for t in translated]},
                             ensure_ascii=False).encode('utf-8')
            self.send_response(200)
            cors_headers(self)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        except urllib.error.HTTPError as e:
            self._send_error(500, 'Translation service error (%d).' % e.code)
        except Exception as e:
            self._send_error(500, 'Server error: %s' % str(e))

    def _send_error(self, code, message):
        self.send_response(code)
        cors_headers(self)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.end_headers()
        self.wfile.write(message.encode('utf-8'))
