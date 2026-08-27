# local-golinks

Personal `go/` links for macOS. Type `go/gmail` in your browser and land on the
URL you saved for it — the classic corporate go-links experience, but running
entirely on your own machine. No account, no cloud, no browser extension.

- **Local & private** — a tiny redirect server bound to `127.0.0.1`.
- **No dependencies** — Python 3 standard library only.
- **Editable anywhere** — manage links in a web form at `go/`, or hand-edit a
  plain JSON file.
- **Parameterized links** — `go/jira/ABC-123` → `…/browse/ABC-123`.

## Requirements

- macOS (uses `launchd`)
- Python 3 (ships with the Xcode Command Line Tools: `xcode-select --install`)

## Install

```bash
git clone https://github.com/<you>/local-golinks.git
cd local-golinks
./install.sh
```

The installer will ask for your password (`sudo`) to do two things:

1. Add `127.0.0.1 go` to `/etc/hosts` so the bare hostname `go` resolves locally.
2. Install a `launchd` daemon that runs the server on port 80 (binding a port
   below 1024 requires root; the server itself only listens on `127.0.0.1`).

Then open <http://go/>.

## Usage

- **Follow a link:** type `go/gmail` in the address bar. Include the trailing
  `/` (or a shortcut) so the browser treats it as a URL, not a search.
- **Manage links:** open `go/` for the add / **edit** / **delete** form.
- **Add the current page:** drag the **“+ go link”** button from `go/` to your
  bookmarks bar, then click it on any page to pre-fill the form.
- **Missing link:** visiting an unknown `go/foo` drops you on the form with
  `foo` pre-filled, cursor in the URL field — just type the destination.
- **Pin the tab:** open `go/`, right-click the tab → Pin Tab (orange “go” icon).
- **Hand-edit:** open `links.json` — plain `"shortcut": "url"` JSON. Changes
  take effect immediately, no restart.

### Parameterized links

A `%s` placeholder is filled from the trailing path:

```json
"jira": "https://jira.example.com/browse/%s"
```

→ `go/jira/ABC-123` opens `…/browse/ABC-123`.

Without `%s`, extra path is appended: `"docs": "https://docs.example.com"` makes
`go/docs/api` → `https://docs.example.com/api`.

## Managing the daemon

```bash
# restart after editing server.py
sudo launchctl kickstart -k system/local.golinks

# stop / start
sudo launchctl unload /Library/LaunchDaemons/local.golinks.plist
sudo launchctl load -w /Library/LaunchDaemons/local.golinks.plist

# logs
tail -f golinks.log
```

## Uninstall

```bash
./uninstall.sh          # removes the daemon, keeps your links.json
./uninstall.sh --hosts  # also removes the '127.0.0.1 go' hosts entry
```

## Configuration

The server reads these environment variables (set in the plist template):

| Variable       | Default              | Purpose                     |
| -------------- | -------------------- | --------------------------- |
| `GOLINKS_DB`   | `./links.json`       | Path to the link store      |
| `GOLINKS_HOST` | `127.0.0.1`          | Bind address                |
| `GOLINKS_PORT` | `80`                 | Bind port                   |

To try it without installing anything (no sudo, high port):

```bash
GOLINKS_PORT=8899 python3 server.py
# then visit http://127.0.0.1:8899/
```

## How it works

`server.py` is a ~300-line `http.server` app. A request to `/<shortcut>` looks
the shortcut up in `links.json` and returns a `302` redirect; unknown paths fall
through to the add form. The `go` hostname resolves to `127.0.0.1` via
`/etc/hosts`, and `launchd` keeps the server running and starts it at boot.

## Notes & caveats

- Redirects are served over `http://` (not https) — fine for local hops.
- The daemon runs as root only to bind port 80; it listens on loopback only, so
  nothing is exposed off your machine.
- `links.json` is kept world-writable (`0666`) so you can hand-edit it even
  though root owns the process.
- macOS only. The redirect server is portable, but the installer and service
  wiring assume `launchd`.

## License

MIT — see [LICENSE](LICENSE).
