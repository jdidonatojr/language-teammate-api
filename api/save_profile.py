"""
Language Teammate — /api/save_profile  (cloned from ESource Course Developer; own store)  (POST, GET)
(Property of Westfield Enterprises — part of the LumaQuest project.)

Learner memory for the course platform (POC):
  - Stores one small JSON file per learner per course in the PRIVATE
    source-library repo, under  profiles/<email>/<course>.json
  - Holds the intake answers (level, goal), progress, sessions, the
    language used, and the latest Knowledge Check score.
  - Saves MERGE into the existing file, so a slide update never wipes
    the intake answers, and vice versa.
  - To honor a deletion request, delete the learner's folder from the
    repo by hand. Nothing else stores their data.

v1.1 (dashboard prep) — new optional fields on "save":
  highest_slide   int   furthest slide reached (never goes down)
  total_slides    int   how many slides the course has (sets "completed")
  language        str   language code the learner chose (en, es, de ...)
  session         1     "a new session started" -> sessions += 1,
                        first_seen set on the very first one
  quiz_right, quiz_total  ints  Knowledge Check result -> quiz block
                        {right, total, score, best, attempts, when}

Fields kept for the dashboard: email, course, level, goal, last_slide,
highest_slide, total_slides, completed, completed_at, language,
sessions, first_seen, updated (= last seen), quiz.

Actions (JSON POST body):
  {"action": "save", "email": "...", "course": "1000",
   "level": 2, "goal": "close deals faster", "last_slide": 14}
      -> any field may be sent alone
  {"action": "get", "email": "...", "course": "1000"}
      -> {"ok": true, "profile": {...}}  or  {"ok": true, "profile": null}

Browser test (GET):
  /api/save_profile?email=you@company.com&course=1000
      -> same reply as action "get"

Vercel environment variables:
  GITHUB_TOKEN     — token that can read/write the private repo (already set)
  PROFILES_REPO    — optional, default jdidonatojr/language-teammate-private
  PROFILES_PATH    — optional, default profiles
"""

import os
import re
import json
import base64
import time
import urllib.parse
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler

GH_API = 'https://api.github.com'
DEFAULT_REPO = 'jdidonatojr/language-teammate-private'
DEFAULT_PATH = 'profiles'
BRANCH = 'main'


def cors(handler):
    handler.send_header('Access-Control-Allow-Origin', '*')
    handler.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
    handler.send_header('Access-Control-Allow-Headers', 'Content-Type')


def gh_headers(token):
    return {'Authorization': f'Bearer {token}',
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
            'User-Agent': 'language-teammate-profile-store',
            'Content-Type': 'application/json'}


def norm_email(e):
    return str(e or '').strip().lower()


def valid_email(e):
    return bool(re.match(r'^[^@\s/\\]+@[^@\s/\\]+\.[^@\s/\\]+$', e))


def profile_path(email, course):
    root = (os.environ.get('PROFILES_PATH', '').strip() or DEFAULT_PATH).strip('/')
    return '%s/%s/%s.json' % (root, email, course)


def repo():
    return os.environ.get('PROFILES_REPO', '').strip() or DEFAULT_REPO


def gh_read(token, path):
    """Return (profile_dict_or_None, sha_or_None)."""
    url = f'{GH_API}/repos/{repo()}/contents/{urllib.parse.quote(path)}?ref={BRANCH}'
    req = urllib.request.Request(url, headers=gh_headers(token))
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            meta = json.loads(resp.read().decode('utf-8'))
        text = base64.b64decode(meta.get('content', '')).decode('utf-8', 'replace')
        try:
            data = json.loads(text)
        except Exception:
            data = None
        return (data if isinstance(data, dict) else None), meta.get('sha')
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None, None
        raise


def gh_write(token, path, obj, sha):
    text = json.dumps(obj, indent=2, ensure_ascii=False) + '\n'
    body = {'message': 'Update learner profile',
            'content': base64.b64encode(text.encode('utf-8')).decode('ascii'),
            'branch': BRANCH}
    if sha:
        body['sha'] = sha
    url = f'{GH_API}/repos/{repo()}/contents/{urllib.parse.quote(path)}'
    req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'),
                                 headers=gh_headers(token), method='PUT')
    urllib.request.urlopen(req, timeout=30).read()


def to_int(v):
    try:
        return int(v)
    except Exception:
        return None


def handle(params):
    """Shared logic for POST bodies and GET query strings."""
    token = os.environ.get('GITHUB_TOKEN', '').strip()
    if not token:
        return 500, {'ok': False, 'message': 'Profile store is not configured on the server.'}

    action = str(params.get('action', '') or 'get').strip()
    email = norm_email(params.get('email'))
    course = re.sub(r'\D', '', str(params.get('course', '')))
    if not valid_email(email):
        return 400, {'ok': False, 'message': 'A valid email is required.'}
    if not course:
        return 400, {'ok': False, 'message': 'A course number is required.'}

    path = profile_path(email, course)

    if action == 'get':
        try:
            profile, _sha = gh_read(token, path)
        except Exception:
            return 500, {'ok': False, 'message': 'The profile could not be read. Try again shortly.'}
        return 200, {'ok': True, 'profile': profile}

    if action == 'save':
        try:
            profile, sha = gh_read(token, path)
        except Exception:
            profile, sha = None, None
        if not isinstance(profile, dict):
            profile = {}

        now = int(time.time())
        changed = False

        # ---- intake answers ----
        if params.get('level') is not None:
            lv = to_int(params['level'])
            if lv is not None and 1 <= lv <= 5:
                profile['level'] = lv
                changed = True
        if params.get('goal') is not None:
            goal = str(params['goal']).strip()[:500]
            if goal:
                profile['goal'] = goal
                changed = True

        # ---- progress ----
        if params.get('last_slide') is not None:
            ls = to_int(params['last_slide'])
            if ls is not None and ls >= 1:
                profile['last_slide'] = ls
                # highest_slide never goes down
                if ls > int(profile.get('highest_slide', 0) or 0):
                    profile['highest_slide'] = ls
                changed = True
        if params.get('highest_slide') is not None:
            hs = to_int(params['highest_slide'])
            if hs is not None and hs >= 1 and hs > int(profile.get('highest_slide', 0) or 0):
                profile['highest_slide'] = hs
                changed = True
        if params.get('total_slides') is not None:
            ts = to_int(params['total_slides'])
            if ts is not None and ts >= 1:
                profile['total_slides'] = ts
                changed = True

        # completed = reached the last slide (set once, never unset)
        ts = int(profile.get('total_slides', 0) or 0)
        hs = int(profile.get('highest_slide', 0) or 0)
        if ts and hs >= ts and not profile.get('completed'):
            profile['completed'] = True
            profile['completed_at'] = now
            changed = True

        # ---- language ----
        if params.get('language') is not None:
            lang = re.sub(r'[^a-zA-Z\-]', '', str(params['language']))[:8].lower()
            if lang:
                profile['language'] = lang
                changed = True

        # ---- sessions ----
        if str(params.get('session', '')).strip() in ('1', 'true', 'yes'):
            profile['sessions'] = int(profile.get('sessions', 0) or 0) + 1
            profile.setdefault('first_seen', now)
            changed = True

        # ---- Knowledge Check ----
        qr = to_int(params.get('quiz_right'))
        qt = to_int(params.get('quiz_total'))
        if qr is not None and qt is not None and qt >= 1 and 0 <= qr <= qt:
            score = int(round(qr * 100.0 / qt))
            quiz = profile.get('quiz') if isinstance(profile.get('quiz'), dict) else {}
            quiz['right'] = qr
            quiz['total'] = qt
            quiz['score'] = score
            quiz['best'] = max(score, int(quiz.get('best', 0) or 0))
            quiz['attempts'] = int(quiz.get('attempts', 0) or 0) + 1
            quiz['when'] = now
            profile['quiz'] = quiz
            changed = True

        if not changed:
            return 400, {'ok': False, 'message': 'Nothing to save.'}

        profile['email'] = email
        profile['course'] = course
        profile.setdefault('first_seen', now)
        profile['updated'] = now
        try:
            gh_write(token, path, profile, sha)
        except Exception:
            return 500, {'ok': False, 'message': 'The profile could not be saved. Try again shortly.'}
        return 200, {'ok': True}

    return 400, {'ok': False, 'message': 'Unknown action.'}


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        cors(self)
        self.end_headers()

    def do_GET(self):
        try:
            qs = urllib.parse.urlparse(self.path).query
            params = {k: v[0] for k, v in urllib.parse.parse_qs(qs).items()}
            params.setdefault('action', 'get')
            status, obj = handle(params)
        except Exception:
            status, obj = 500, {'ok': False, 'message': 'Server error.'}
        self._send(status, obj)

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            params = json.loads(self.rfile.read(length).decode('utf-8')) if length else {}
            status, obj = handle(params)
        except Exception:
            status, obj = 500, {'ok': False, 'message': 'Server error.'}
        self._send(status, obj)

    def _send(self, status, obj):
        body = json.dumps(obj).encode('utf-8')
        self.send_response(status)
        cors(self)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
