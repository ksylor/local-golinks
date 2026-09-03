#!/usr/bin/env python3
"""
golinks - a tiny personal go/ redirect server.

Type http://go/<shortcut> in your browser and get redirected to the URL
you stored for that shortcut. Manage links at http://go/ (edit page) or by
hand-editing the JSON store.

No third-party dependencies -- Python 3 stdlib only.

DISCLAIMER: This code was generated almost entirely by an AI assistant and is
provided AS-IS, with NO WARRANTY OR GUARANTEE OF ANY KIND (correctness,
security, or fitness for any purpose). It has not been professionally audited
and runs a server as root. Review it yourself and use it at your own risk.
See the LICENSE file for the full disclaimer.

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
import re
import sys
import tempfile
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(HERE, "templates")
DB_PATH = os.environ.get("GOLINKS_DB", os.path.join(HERE, "links.json"))
HOST = os.environ.get("GOLINKS_HOST", "127.0.0.1")
PORT = int(os.environ.get("GOLINKS_PORT", "80"))

# Shortcuts that are reserved for the app itself and cannot be used as links.
RESERVED = {"", "_edit", "_add", "_delete", "_api", "_go", "favicon.ico", "robots.txt"}

# Hostnames this server will answer to. Anything else (e.g. an attacker's
# domain rebinding to 127.0.0.1) is refused, to blunt DNS-rebinding attacks.
ALLOWED_HOSTS = {"go", "localhost", "127.0.0.1", HOST}

# Schemes a stored link may use. Blocks javascript:/data: and similar.
ALLOWED_SCHEMES = ("http", "https", "mailto", "ftp")

# Explicitly dangerous schemes: never prepend a default scheme to these, so
# they fail validation instead of being silently rewritten into junk URLs.
DANGEROUS_SCHEMES = ("javascript", "data", "vbscript", "file", "blob")

_lock = threading.Lock()


def has_control_chars(s):
    return any(ord(c) < 0x20 or ord(c) == 0x7F for c in s)


def normalize_target(url):
    """Add a default scheme to a bare host, leaving valid schemes/paths alone."""
    if url.startswith("/"):
        return url
    scheme = url.split(":", 1)[0].lower() if ":" in url else ""
    if scheme in ALLOWED_SCHEMES or scheme in DANGEROUS_SCHEMES:
        return url
    # No usable scheme (bare host, or host:port) -> assume https.
    return "https://" + url


def valid_target(url):
    """A storable redirect target: no control chars, allowed scheme or root-relative."""
    if not url or has_control_chars(url):
        return False
    if url.startswith("/"):
        return True
    scheme = url.split(":", 1)[0].lower() if ":" in url else ""
    return scheme in ALLOWED_SCHEMES


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


_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


def render_template(name, **values):
    """Fill {{name}} placeholders in templates/<name> from `values`.

    Templates are read fresh each request so edits show up without a restart.
    Substitution is a single pass (values are not re-scanned), so inserted
    content can never expand into further placeholders -- callers are still
    responsible for HTML-escaping any untrusted values they pass in.
    Template names are fixed by the server (never user input), so there is no
    path-traversal surface here.
    """
    with open(os.path.join(TEMPLATES_DIR, name), "r", encoding="utf-8") as f:
        template = f.read()
    return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), ""), template)


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
    # Suggestions for the search box's native <datalist>. The URL is shown as
    # the option's label hint; both fields are HTML-escaped.
    options = "".join(
        '<option value="{v}">{u}</option>'.format(v=html.escape(k), u=html.escape(links[k]))
        for k in sorted(links)
    )
    msg = "<p class='sub' style='color:#2563eb'>{}</p>".format(html.escape(message)) if message else ""
    # Focus the search box on the plain page; once a shortcut is prefilled
    # (missing link or edit), focus the add form's URL field instead.
    plain = not prefill_key and not prefill_url
    search_focus = " autofocus" if plain else ""
    focus_url = bool(prefill_key)
    key_focus = "" if (focus_url or plain) else " autofocus"
    url_focus = " autofocus" if focus_url else ""
    orig_field = (
        '\n  <input type=hidden name=orig value="{}">'.format(html.escape(orig)) if orig else ""
    )
    return render_template(
        "edit.html",
        style=render_template("style.css"),
        count=str(len(links)),
        plural="" if len(links) == 1 else "s",
        db=html.escape(DB_PATH),
        msg=msg,
        options=options,
        search_focus=search_focus,
        orig_field=orig_field,
        pk=html.escape(prefill_key),
        pu=html.escape(prefill_url),
        key_focus=key_focus,
        url_focus=url_focus,
        save_label="Update" if orig else "Save",
        table=table,
        bm=html.escape(BOOKMARKLET, quote=True),
    )


class Handler(BaseHTTPRequestHandler):
    server_version = "golinks/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("golinks: %s - %s\n" % (self.address_string(), fmt % args))

    def _host_ok(self):
        """Reject requests whose Host isn't one we recognize (DNS-rebinding guard)."""
        host = self.headers.get("Host", "")
        hostname = host.rsplit(":", 1)[0] if host else ""
        return hostname in ALLOWED_HOSTS

    def _same_origin(self):
        """Block cross-site state changes (CSRF). Non-browser clients (no
        Origin/Referer/Sec-Fetch-Site) are allowed; browsers always send at
        least Sec-Fetch-Site on modern versions."""
        site = self.headers.get("Sec-Fetch-Site")
        if site is not None:
            return site in ("same-origin", "same-site", "none")
        src = self.headers.get("Origin") or self.headers.get("Referer")
        if not src:
            return True
        return urlparse(src).hostname in ALLOWED_HOSTS

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

    def _serve_shortcut(self, lookup, extra_query=""):
        """Resolve `lookup` against the store and 302 to it. Returns True if a
        redirect was sent, False if no matching shortcut exists."""
        target = resolve(lookup)
        if not target:
            return False
        if extra_query and "%s" not in target:
            sep = "&" if "?" in target else "?"
            target = target + sep + extra_query
        # Refuse to emit a Location built from a tampered/hand-edited entry
        # containing control chars (HTTP response-splitting guard).
        if has_control_chars(target):
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Invalid redirect target")
            return True
        self._redirect(target)
        return True

    def do_GET(self):
        if not self._host_ok():
            self.send_error(HTTPStatus.FORBIDDEN, "Invalid Host header")
            return

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

        if first == "_go":
            # Search-box submit: jump to the chosen/typed shortcut.
            to = parse_qs(parsed.query).get("to", [""])[0].strip().strip("/")
            if to and self._serve_shortcut(to):
                return
            # Empty -> plain form; unknown -> prefilled create form (never an
            # open redirect: we only ever redirect to a stored shortcut).
            if not to:
                self._send_html(render_edit())
            else:
                self._send_html(
                    render_edit(
                        prefill_key=to,
                        message="No link for '%s' yet — add it below." % to,
                    ),
                    status=HTTPStatus.NOT_FOUND,
                )
            return

        if first == "_api":
            data = json.dumps(load_links(), indent=2, sort_keys=True).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        # Otherwise, treat the path as a shortcut lookup (carrying the query).
        if self._serve_shortcut(path, parsed.query):
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
        if not self._host_ok():
            self.send_error(HTTPStatus.FORBIDDEN, "Invalid Host header")
            return
        if not self._same_origin():
            self.send_error(HTTPStatus.FORBIDDEN, "Cross-origin request blocked")
            return

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
            url = normalize_target(url)
            if not valid_target(url):
                self._send_html(
                    render_edit(
                        key,
                        url,
                        "That URL isn't allowed — use http/https/mailto/ftp and no control characters.",
                        orig=orig,
                    ),
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
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
