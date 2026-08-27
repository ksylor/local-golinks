#!/usr/bin/env python3
"""
golinks - a tiny personal go/ redirect server.

Type http://go/<shortcut> in your browser and get redirected to the URL
you stored for that shortcut. Manage links at http://go/ (edit page) or by
hand-editing the JSON store.

No third-party dependencies -- Python 3 stdlib only.

Environment:
  GOLINKS_DB    path to the JSON store (default: ./links.json next to this file)
  GOLINKS_HOST  bind address (default: 127.0.0.1)
  GOLINKS_PORT  bind port    (default: 80)

Link values may contain a "%s" placeholder for parameterized links, e.g.
  "jira": "https://jira.example.com/browse/%s"
so that go/jira/ABC-123 -> https://jira.example.com/browse/ABC-123
Otherwise any extra path after a matched shortcut is appended, so
  "docs": "https://docs.example.com"  =>  go/docs/api -> https://docs.example.com/api
"""

import html
import json
import os
import sys
import tempfile
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("GOLINKS_DB", os.path.join(HERE, "links.json"))
HOST = os.environ.get("GOLINKS_HOST", "127.0.0.1")
PORT = int(os.environ.get("GOLINKS_PORT", "80"))

# Shortcuts that are reserved for the app itself and cannot be used as links.
RESERVED = {"", "_edit", "_add", "_delete", "_api", "favicon.ico", "robots.txt"}

_lock = threading.Lock()


def load_links():
    try:
        with open(DB_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except FileNotFoundError:
        pass
    except (json.JSONDecodeError, ValueError):
        sys.stderr.write("golinks: warning: %s is not valid JSON\n" % DB_PATH)
    return {}


def save_links(links):
    with _lock:
        tmp_fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(DB_PATH) or ".")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(links, f, indent=2, sort_keys=True)
                f.write("\n")
            os.replace(tmp_path, DB_PATH)
            # Keep the store hand-editable even though the daemon runs as root.
            try:
                os.chmod(DB_PATH, 0o666)
            except OSError:
                pass
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


def resolve(path):
    """Resolve a request path like 'foo/bar' to a target URL, or None."""
    links = load_links()
    path = path.strip("/")
    if not path:
        return None
    segs = path.split("/")
    for i in range(len(segs), 0, -1):
        key = "/".join(segs[:i])
        if key in links:
            target = links[key]
            rest = "/".join(segs[i:])
            if "%s" in target:
                return target.replace("%s", quote(rest))
            if rest:
                return target.rstrip("/") + "/" + rest
            return target
    return None


PAGE_STYLE = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       max-width: 820px; margin: 40px auto; padding: 0 20px;
       color: #1a1a1a; background: #fff; }
@media (prefers-color-scheme: dark) {
  body { color: #e6e6e6; background: #16171a; }
  input { background: #23252b; color: #e6e6e6; border-color: #3a3d45 !important; }
  tr:hover td { background: #1e2024; }
  code { background: #23252b; }
  a { color: #6ea8fe; }
}
h1 { font-size: 22px; margin: 0 0 4px; }
.sub { color: #888; margin: 0 0 24px; font-size: 13px; }
form.add { display: flex; gap: 8px; margin: 0 0 24px; flex-wrap: wrap; }
input { padding: 8px 10px; border: 1px solid #ccc; border-radius: 7px; font: inherit; }
input[name=key] { width: 160px; }
input[name=url] { flex: 1; min-width: 220px; }
button { padding: 8px 14px; border: 0; border-radius: 7px; font: inherit;
         background: #2563eb; color: #fff; cursor: pointer; }
button.del { background: transparent; color: #d33; padding: 4px 8px; }
td.actions { white-space: nowrap; }
td.actions form { display: inline; }
a.edit { display: inline-block; padding: 4px 8px; color: #2563eb; text-decoration: none; }
@media (prefers-color-scheme: dark) { a.edit { color: #6ea8fe; } }
table { width: 100%; border-collapse: collapse; }
th { text-align: left; font-size: 12px; text-transform: uppercase; letter-spacing: .04em;
     color: #999; border-bottom: 1px solid #ddd; padding: 6px 8px; }
td { padding: 8px; border-bottom: 1px solid #eee; vertical-align: top; }
@media (prefers-color-scheme: dark) { td, th { border-color: #2a2c31; } }
td.key { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; white-space: nowrap; }
td.url { word-break: break-all; }
code { background: #f2f2f4; padding: 1px 5px; border-radius: 4px;
       font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px; }
.empty { color: #999; padding: 24px 8px; }
.bm { margin-top: 32px; font-size: 13px; color: #888; }
.bm a { display: inline-block; padding: 6px 12px; border: 1px dashed #bbb;
        border-radius: 7px; text-decoration: none; color: inherit; }
"""

# A tiny "go" favicon (SVG) so a pinned tab is recognizable.
FAVICON_SVG = (
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'>"
    "<rect width='100' height='100' rx='22' fill='#f97316'/>"
    "<text x='50' y='54' font-family='-apple-system,Helvetica,Arial,sans-serif' "
    "font-size='52' font-weight='700' fill='#fff' text-anchor='middle' "
    "dominant-baseline='central'>go</text></svg>"
)

# Bookmarklet: opens the add form pre-filled with the current tab's URL + title.
BOOKMARKLET = (
    "javascript:(function(){var u=encodeURIComponent(location.href),"
    "t=encodeURIComponent(document.title);"
    "window.open('http://go/_add?url='+u+'&title='+t,'_blank');})()"
)


def render_edit(prefill_key="", prefill_url="", message="", orig=""):
    links = load_links()
    rows = []
    for key in sorted(links):
        url = links[key]
        rows.append(
            "<tr><td class='key'><a href='http://go/{k}'>{k}</a></td>"
            "<td class='url'><a href='{u}'>{u}</a></td>"
            "<td class='actions'>"
            "<a class='edit' href='/_edit?key={qk}&url={qu}'>edit</a>"
            "<form method='post' action='/_delete' "
            "onsubmit=\"return confirm('Delete {k}?')\">"
            "<input type='hidden' name='key' value='{k}'>"
            "<button class='del' type='submit'>delete</button></form></td></tr>".format(
                k=html.escape(key),
                u=html.escape(url),
                qk=quote(key, safe=""),
                qu=quote(url, safe=""),
            )
        )
    table = (
        "<table><tr><th>go/</th><th>redirects to</th><th></th></tr>{}</table>".format("".join(rows))
        if rows
        else "<p class='empty'>No links yet. Add your first one above.</p>"
    )
    msg = "<p class='sub' style='color:#2563eb'>{}</p>".format(html.escape(message)) if message else ""
    # If the shortcut is already filled (arriving from a missing link or an
    # edit), focus the URL field so you can type/change the destination.
    focus_url = bool(prefill_key)
    key_focus = "" if focus_url else " autofocus"
    url_focus = " autofocus" if focus_url else ""
    orig_field = (
        "\n  <input type=hidden name=orig value=\"{}\">".format(html.escape(orig)) if orig else ""
    )
    save_label = "Update" if orig else "Save"
    return """<!doctype html><html><head><meta charset=utf-8>
<title>go/ links</title><meta name=viewport content="width=device-width,initial-scale=1">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<style>{style}</style></head><body>
<h1>go/ links</h1>
<p class=sub>{count} link{plural} &middot; stored in <code>{db}</code></p>
{msg}
<form class=add method=post action=/_add>{orig_field}
  <input name=key placeholder="shortcut" value="{pk}"{key_focus} autocomplete=off>
  <input name=url placeholder="https://..." value="{pu}"{url_focus} autocomplete=off>
  <button type=submit>{save_label}</button>
</form>
{table}
<p class=bm>Drag this to your bookmarks bar to add the current page:
<a href="{bm}">+ go link</a></p>
</body></html>""".format(
        style=PAGE_STYLE,
        count=len(links),
        plural="" if len(links) == 1 else "s",
        db=html.escape(DB_PATH),
        msg=msg,
        pk=html.escape(prefill_key),
        pu=html.escape(prefill_url),
        key_focus=key_focus,
        url_focus=url_focus,
        orig_field=orig_field,
        save_label=save_label,
        table=table,
        bm=html.escape(BOOKMARKLET, quote=True),
    )


class Handler(BaseHTTPRequestHandler):
    server_version = "golinks/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("golinks: %s - %s\n" % (self.address_string(), fmt % args))

    def _send_html(self, body, status=HTTPStatus.OK):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, url, status=HTTPStatus.FOUND):
        self.send_response(status)
        self.send_header("Location", url)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        first = path.strip("/").split("/")[0]

        if path in ("/favicon.svg", "/favicon.ico"):
            data = FAVICON_SVG.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            self.wfile.write(data)
            return

        if path == "/robots.txt":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return

        if path == "/" or first == "_edit":
            q = parse_qs(parsed.query)
            key = q.get("key", [""])[0]
            url = q.get("url", [""])[0]
            if key:
                # Editing an existing link: prefill both fields, remember the
                # original key so a rename can remove the old entry.
                self._send_html(render_edit(prefill_key=key, prefill_url=url, orig=key))
            else:
                self._send_html(render_edit())
            return

        if first == "_add":
            q = parse_qs(parsed.query)
            url = (q.get("url", [""])[0])
            self._send_html(render_edit(prefill_url=unquote(url)))
            return

        if first == "_api":
            data = json.dumps(load_links(), indent=2, sort_keys=True).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        # Otherwise, treat the path as a shortcut lookup.
        target = resolve(path)
        if target:
            # Carry through any query string on the incoming request.
            if parsed.query and "%s" not in target:
                sep = "&" if "?" in target else "?"
                target = target + sep + parsed.query
            self._redirect(target)
            return

        # Unknown shortcut -> edit page prefilled so you can create it.
        self._send_html(
            render_edit(
                prefill_key=unquote(path.strip("/")),
                message="No link for '%s' yet — add it below." % path.strip("/"),
            ),
            status=HTTPStatus.NOT_FOUND,
        )

    def do_POST(self):
        parsed = urlparse(self.path)
        first = parsed.path.strip("/").split("/")[0]
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length).decode("utf-8") if length else ""
        form = {k: v[0] for k, v in parse_qs(body).items()}

        if first == "_add":
            key = form.get("key", "").strip().strip("/")
            url = form.get("url", "").strip()
            orig = form.get("orig", "").strip().strip("/")
            if not key or not url:
                self._send_html(
                    render_edit(key, url, "Both a shortcut and a URL are required.", orig=orig),
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            if key in RESERVED:
                self._send_html(
                    render_edit(url=url, message="'%s' is reserved." % key, orig=orig),
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            if "://" not in url and not url.startswith("/"):
                url = "https://" + url
            links = load_links()
            links[key] = url
            # Renaming an existing shortcut: drop the old key.
            if orig and orig != key and orig in links:
                del links[orig]
            save_links(links)
            self._redirect("/_edit", status=HTTPStatus.SEE_OTHER)
            return

        if first == "_delete":
            key = form.get("key", "").strip().strip("/")
            links = load_links()
            if key in links:
                del links[key]
                save_links(links)
            self._redirect("/_edit", status=HTTPStatus.SEE_OTHER)
            return

        self.send_response(HTTPStatus.NOT_FOUND)
        self.end_headers()


def main():
    # Ensure the store exists and is world-writable so it stays hand-editable.
    if not os.path.exists(DB_PATH):
        save_links({})
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    sys.stderr.write("golinks: serving on http://%s:%d  (db: %s)\n" % (HOST, PORT, DB_PATH))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
