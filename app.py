import atexit
import hashlib
import ipaddress
import re
import os
import uuid
import queue
import threading
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, request, jsonify, send_from_directory, abort, session
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

app = Flask(__name__, static_folder="static")
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

ARCHIVE_DIR = Path("archives")
ARCHIVE_DIR.mkdir(exist_ok=True)

# --- Auth & limits via env vars ---
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")  # set in Railway env
DAILY_LIMIT = int(os.environ.get("DAILY_LIMIT", "50"))

limiter = Limiter(get_remote_address, app=app, default_limits=[])
_daily_counter = {"count": 0, "date": ""}


def check_daily_limit():
    from datetime import date
    today = date.today().isoformat()
    if _daily_counter["date"] != today:
        _daily_counter["date"] = today
        _daily_counter["count"] = 0
    if _daily_counter["count"] >= DAILY_LIMIT:
        return False
    _daily_counter["count"] += 1
    return True


def _pw_hash():
    return hashlib.sha256(APP_PASSWORD.encode()).hexdigest()[:16]


def require_auth():
    if not APP_PASSWORD:
        return None  # no password set = open (local dev)
    if session.get("authed") == _pw_hash():
        return None
    session.pop("authed", None)
    return jsonify(error="Unauthorized"), 401

GOOGLEBOT_UA = (
    "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/127.0.6533.99 Mobile Safari/537.36 "
    "(compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
)

# --- Playwright on a dedicated thread (sync API is single-thread-bound) ---
_work_queue = queue.Queue()


def _browser_thread():
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)

    while True:
        item = _work_queue.get()
        if item is None:  # shutdown sentinel
            break
        url, result_queue = item
        try:
            context = browser.new_context(
                user_agent=GOOGLEBOT_UA,
                extra_http_headers={
                    "Referer": "https://www.google.com/",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                java_script_enabled=True,
                bypass_csp=True,
            )
            page = context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(3000)
                page.evaluate("""() => {
                    const selectors = [
                        '[class*="paywall"]', '[id*="paywall"]',
                        '[class*="modal"]', '[id*="modal"]',
                        '[class*="gate"]', '[id*="gate"]',
                        '[class*="overlay"]', '[id*="overlay"]',
                        '[class*="piano"]', '[id*="piano"]',
                        '[class*="metering"]', '[id*="metering"]',
                        '[class*="subscribe"]', '[id*="subscribe"]',
                        '[class*="regwall"]', '[id*="regwall"]',
                        '[aria-modal="true"]',
                    ];
                    for (const sel of selectors) {
                        document.querySelectorAll(sel).forEach(el => {
                            const style = window.getComputedStyle(el);
                            if (style.position === 'fixed' || style.position === 'sticky' ||
                                style.position === 'absolute' || style.zIndex > 100) {
                                el.remove();
                            }
                        });
                    }
                    document.body.style.overflow = 'auto';
                    document.body.style.position = 'static';
                    document.documentElement.style.overflow = 'auto';
                    document.querySelectorAll('*').forEach(el => {
                        const style = window.getComputedStyle(el);
                        if ((style.position === 'fixed' || style.position === 'sticky') &&
                            parseInt(style.zIndex) > 999) {
                            el.remove();
                        }
                    });
                    document.querySelectorAll('[class*="truncat"], [class*="collapsed"], [class*="hidden"]').forEach(el => {
                        el.style.maxHeight = 'none';
                        el.style.overflow = 'visible';
                    });
                }""")
                html = page.content()
            finally:
                context.close()
            result_queue.put(("ok", html))
        except Exception as e:
            result_queue.put(("error", str(e)))

    browser.close()
    pw.stop()


_thread = threading.Thread(target=_browser_thread, daemon=True)
_thread.start()


def _shutdown():
    _work_queue.put(None)
    _thread.join(timeout=5)


atexit.register(_shutdown)


def fetch_page(url):
    result_q = queue.Queue()
    _work_queue.put((url, result_q))
    status, data = result_q.get(timeout=60)
    if status == "error":
        raise RuntimeError(data)
    return data


def inject_base_tag(html, url):
    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    base_tag = f'<base href="{base_url}/">'
    if re.search(r"<head[^>]*>", html, re.IGNORECASE):
        html = re.sub(r"(<head[^>]*>)", rf"\1{base_tag}", html, count=1, flags=re.IGNORECASE)
    else:
        html = base_tag + html
    return html


def is_safe_url(url):
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    hostname = parsed.hostname
    if not hostname:
        return False
    try:
        ip = ipaddress.ip_address(hostname)
        return ip.is_global
    except ValueError:
        blocked = ("localhost", "127.0.0.1", "0.0.0.0", "[::1]")
        return hostname not in blocked


@app.route("/")
def index():
    if APP_PASSWORD and not session.get("authed"):
        return send_from_directory("static", "login.html")
    return send_from_directory("static", "index.html")


@app.route("/login", methods=["POST"])
@limiter.limit("10/minute")
def login():
    password = request.json.get("password", "") if request.is_json else ""
    if password == APP_PASSWORD:
        session["authed"] = _pw_hash()
        return jsonify(ok=True)
    return jsonify(error="Wrong password"), 403


@app.route("/fetch")
@limiter.limit("5/minute")
def fetch():
    auth = require_auth()
    if auth:
        return auth
    if not check_daily_limit():
        return jsonify(error=f"Daily limit reached ({DAILY_LIMIT} requests)"), 429

    url = request.args.get("url", "").strip()
    if not url:
        return jsonify(error="Missing url parameter"), 400
    if not is_safe_url(url):
        return jsonify(error="URL blocked: must be public http/https"), 400

    try:
        html = fetch_page(url)
    except Exception as e:
        return jsonify(error=str(e)), 502

    html = inject_base_tag(html, url)
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/archive", methods=["POST"])
@limiter.limit("5/minute")
def archive():
    auth = require_auth()
    if auth:
        return auth
    if not check_daily_limit():
        return jsonify(error=f"Daily limit reached ({DAILY_LIMIT} requests)"), 429

    url = request.json.get("url", "").strip() if request.is_json else ""
    if not url:
        return jsonify(error="Missing url"), 400
    if not is_safe_url(url):
        return jsonify(error="URL blocked"), 400

    try:
        html = fetch_page(url)
    except Exception as e:
        return jsonify(error=str(e)), 502

    html = inject_base_tag(html, url)
    file_id = str(uuid.uuid4())
    (ARCHIVE_DIR / file_id).write_bytes(html.encode("utf-8"))
    return jsonify(id=file_id, link=f"/view/{file_id}")


@app.route("/view/<file_id>")
def view(file_id):
    try:
        uuid.UUID(file_id)
    except ValueError:
        abort(400)
    path = ARCHIVE_DIR / file_id
    if not path.exists():
        abort(404)
    return path.read_bytes(), 200, {"Content-Type": "text/html; charset=utf-8"}


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
