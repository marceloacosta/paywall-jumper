# Web Archiver

A self-hosted web archiving tool that fetches and renders web pages using a headless browser, preserving a snapshot of the page as it appears to search engine crawlers.

## How it works

- Renders pages with headless Chromium (via Playwright)
- Spoofs Googlebot user-agent and headers
- Strips common overlay elements (modals, sticky banners)
- Injects a `<base>` tag so CSS/images resolve correctly
- Optionally saves snapshots to disk as permalinks

## Disclaimer

**This project is provided for educational and personal archiving purposes only.**

- This tool is **not** intended to bypass, circumvent, or defeat paywalls, digital rights management, or any access control mechanisms.
- Users are solely responsible for ensuring their use complies with all applicable laws, regulations, and the terms of service of any website accessed through this tool.
- Circumventing access controls may violate the Computer Fraud and Abuse Act (CFAA), the Digital Millennium Copyright Act (DMCA), or equivalent laws in your jurisdiction.
- The author(s) of this project do not condone or encourage unauthorized access to copyrighted or restricted content.
- **Use at your own risk.** The author(s) accept no liability for misuse.

## Setup

```bash
python -m venv env
source env/bin/activate
pip install -r requirements.txt
playwright install chromium
python app.py
```

Open `http://localhost:5000`, paste a URL, and click Fetch.

## Deploy (Docker)

```bash
docker build -t web-archiver .
docker run -p 8000:8000 web-archiver
```

Or deploy directly to Railway:

```bash
railway login
railway init
railway up
```

## Stack

- Python / Flask
- Playwright (headless Chromium)
- flask-limiter (rate limiting)
- gunicorn (production server)

## License

MIT
