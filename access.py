"""
Language Teammate — /api/access  (cloned from ESource Course Developer; own allowlist)  (POST)
(Property of Westfield Enterprises — part of the LumaQuest project.)

The course access gate (OTP by email):
  1. action "request": check the email against the allowlist file in the
     PRIVATE source-library repo. If allowed, email a 6-digit code via
     Resend. The reply NEVER reveals whether the email was on the list.
  2. action "verify": check the code. Codes are computed (HMAC of a
     server secret + email + 10-minute time window), so nothing is
     stored server-side. The current and previous window are accepted,
     giving 10-20 minutes of validity.

Called cross-origin from the Yola player pages (CORS enabled).

Vercel environment variables required:
  OTP_SECRET       — long random phrase; the key codes derive from
  RESEND_API_KEY   — Resend sending key
  GITHUB_TOKEN     — token that can read the private allowlist repo
  ALLOWLIST_REPO   — optional, default jdidonatojr/language-teammate-private
  ALLOWLIST_PATH   — optional, default access/emails.txt
  OTP_FROM         — optional, default "Language Teammate <courses@esourceu.com>" (sender domain must be verified in Resend)
"""

import os
import re
import json
import hmac
import base64
import hashlib
import time
import urllib.parse
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler

GH_API = 'https://api.github.com'
DEFAULT_REPO = 'jdidonatojr/language-teammate-private'
DEFAULT_PATH = 'access/emails.txt'
DEFAULT_FROM = 'Language Teammate <courses@esourceu.com>'
WINDOW_SECONDS = 600          # each code window is 10 minutes
GENERIC_OK = 'If that email is on the access list, a code is on its way. Check your inbox (and spam folder).'


def cors(handler):
    handler.send_header('Access-Control-Allow-Origin', '*')
    handler.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
    handler.send_header('Access-Control-Allow-Headers', 'Content-Type')


def norm_email(e):
    return str(e or '').strip().lower()


def load_allowlist():
    token = os.environ.get('GITHUB_TOKEN', '').strip()
    repo = os.environ.get('ALLOWLIST_REPO', '').strip() or DEFAULT_REPO
    path = os.environ.get('ALLOWLIST_PATH', '').strip() or DEFAULT_PATH
    url = f'{GH_API}/repos/{repo}/contents/{urllib.parse.quote(path)}'
    req = urllib.request.Request(url, headers={
        'Authorization': f'Bearer {token}',
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'language-teammate-access-gate',
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        meta = json.loads(resp.read().decode('utf-8'))
    text = base64.b64decode(meta.get('content', '')).decode('utf-8', 'replace')
    entries = []
    for raw in text.splitlines():
        line = raw.strip().lower()
        if not line or line.startswith('#'):
            continue
        entries.append(line)
    return entries


def email_allowed(email, entries):
    for entry in entries:
        if entry.startswith('@'):
            if email.endswith(entry):
                return True
        elif email == entry:
            return True
    return False


def make_code(secret, email, window):
    msg = ('%s|%d' % (email, window)).encode('utf-8')
    digest = hmac.new(secret.encode('utf-8'), msg, hashlib.sha256).digest()
    number = int.from_bytes(digest[:4], 'big') % 1000000
    return '%06d' % number


def send_code(api_key, sender, email, code):
    body = {
        'from': sender,
        'to': [email],
        'subject': 'Your course access code: ' + code,
        'text': ('Your Language Teammate access code is:\n\n'
                 '    ' + code + '\n\n'
                 'It is valid for about 10 minutes. Enter it on the course '
                 'page to continue.\n\n'
                 'If you did not request this, you can ignore this email.\n')
    }
    req = urllib.request.Request(
        'https://api.resend.com/emails',
        data=json.dumps(body).encode('utf-8'),
        headers={'Authorization': 'Bearer ' + api_key,
                 'Content-Type': 'application/json',
                 'Accept': 'application/json',
                 'User-Agent': 'language-teammate-access-gate/1.0'},
        method='POST')
    urllib.request.urlopen(req, timeout=30).read()


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        cors(self)
        self.end_headers()

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            payload = json.loads(self.rfile.read(length).decode('utf-8')) if length else {}

            secret = os.environ.get('OTP_SECRET', '').strip()
            resend_key = os.environ.get('RESEND_API_KEY', '').strip()
            if not secret or not resend_key:
                return self._send(500, {'ok': False,
                                        'message': 'The access gate is not fully configured on the server.'})

            action = str(payload.get('action', '')).strip()
            email = norm_email(payload.get('email'))
            if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
                return self._send(400, {'ok': False, 'message': 'Please enter a valid email address.'})

            if action == 'request':
                try:
                    entries = load_allowlist()
                except Exception:
                    return self._send(500, {'ok': False,
                                            'message': 'The access list could not be read. Please try again shortly.'})
                if email_allowed(email, entries):
                    window = int(time.time() // WINDOW_SECONDS)
                    code = make_code(secret, email, window)
                    try:
                        sender = os.environ.get('OTP_FROM', '').strip() or DEFAULT_FROM
                        send_code(resend_key, sender, email, code)
                    except Exception as e:
                        detail = ''
                        if hasattr(e, 'read'):
                            try:
                                detail = e.read().decode('utf-8', 'replace')[:300]
                            except Exception:
                                pass
                        print('RESEND SEND FAILED:', repr(e), detail)
                        return self._send(500, {'ok': False,
                                                'message': 'The code email could not be sent. Please try again shortly.'})
                # Same reply either way — never reveal list membership.
                return self._send(200, {'ok': True, 'message': GENERIC_OK})

            if action == 'verify':
                code = re.sub(r'\D', '', str(payload.get('code', '')))
                window = int(time.time() // WINDOW_SECONDS)
                valid = False
                for w in (window, window - 1):
                    if hmac.compare_digest(code, make_code(secret, email, w)):
                        valid = True
                        break
                if not valid:
                    return self._send(200, {'ok': False,
                                            'message': 'That code is not right or has expired. Request a fresh one.'})
                return self._send(200, {'ok': True})

            return self._send(400, {'ok': False, 'message': 'Unknown action.'})

        except Exception:
            return self._send(500, {'ok': False, 'message': 'Server error. Please try again.'})

    def _send(self, status, obj):
        body = json.dumps(obj).encode('utf-8')
        self.send_response(status)
        cors(self)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
