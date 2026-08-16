"""Zero-dependency API client for the KAERIS i18n service (stdlib only)."""

import io
import json
import os
import time
import uuid
import zipfile
import urllib.request
import urllib.error
import urllib.parse

DEFAULT_API = "https://kaeris.dev"


class KaerisError(Exception):
    """Any error talking to the KAERIS API."""


def checked_key(key, label):
    """A key ready to travel in an HTTP header, or a clear refusal.

    Surrounding whitespace is stripped: a key pasted from an email or read out of a file
    routinely carries a trailing newline, and http.client rejects that with a ValueError deep
    in the stack — the person sees a traceback about header values and has no idea their key
    is fine apart from one invisible character. Anything else outside printable ASCII (a
    Cyrillic letter that looks Latin, a smart quote from a document) is refused by name here
    rather than as a UnicodeEncodeError, which is what an agent used to get.

    The key itself is NEVER part of the message — the whole point is that it does not leak
    into somebody's transcript or log.
    """
    if key is None:
        return None
    cleaned = str(key).strip()
    if not cleaned:
        return None
    if any(not (32 <= ord(ch) < 127) for ch in cleaned):
        raise KaerisError(
            f"This {label} contains characters that cannot be sent in an HTTP header "
            "(non-ASCII or control characters). Check it was copied whole and without "
            "invisible characters.")
    return cleaned


def checked_api_url(url):
    """The API base URL, refused if it would send secrets in the clear.

    `--api-url` / KAERIS_API_URL exists for local development and self-hosting, and it is also
    the single lever that redirects everything this client sends. Measured 16.08.2026 with a
    stand-in server on the local network: over `http://` the PAYING customer's key arrived in
    the `X-API-Key` header in plain text, together with the contents of the file being
    translated — anyone on the same wifi reads both.

    https is required; http is allowed only for loopback, where there is no wire to listen on
    and where a developer running the backend locally actually needs it. A bare host is read
    as https. The refusal is loud: pointing at another host has to be a visible decision, not
    a silent downgrade.
    """
    raw = (url or "").strip().rstrip("/")
    if not raw:
        raise KaerisError("Empty API URL")
    parsed = urllib.parse.urlsplit(raw if "://" in raw else "https://" + raw)
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "https":
        return raw if "://" in raw else "https://" + raw
    loopback = host in ("localhost", "::1") or host.startswith("127.")
    if parsed.scheme == "http" and loopback:
        return raw
    raise KaerisError(
        f"Refusing to use {raw}: an API URL must be https — over http your API key and the "
        "strings you translate travel in the clear. (http is allowed for localhost only.)")


# One directory level is legitimate: Android output is `values-<lang>/strings.xml`. Everything
# else we produce is a single file name.
MAX_MEMBER_DEPTH = 2


def safe_member_name(name):
    """The name a ZIP member may be written under, or KaerisError if it may not be written.

    Rejects what an archive uses to escape the directory it is unpacked into: absolute paths,
    Windows drive letters and UNC prefixes, backslash separators, any `..` segment, and
    nesting deeper than one directory. Returns the name normalized to forward slashes.
    """
    raw = str(name).replace("\\", "/")
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    bad = (
        not parts
        or raw.startswith("/")
        or ".." in parts
        or (len(raw) > 1 and raw[1] == ":")          # C:\... / C:/...
        or len(parts) > MAX_MEMBER_DEPTH
    )
    if bad:
        raise KaerisError(
            f"Refusing a result archive: the member name {name!r} would write outside the "
            "output directory. Nothing was written."
        )
    return "/".join(parts)


class _SameHostRedirect(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only while it stays on the same host.

    Our credentials travel as X-API-Key / X-OpenRouter-Key headers, and urllib re-sends custom
    headers to whatever a redirect points at. Python strips Authorization across hosts; it has
    no reason to know ours are secrets. Proven by running it: a 302 to another host handed
    over the user's paid key in full.

    An http→https bump on the SAME host is normal (Cloudflare does it) and still allowed. A
    change of host is refused outright rather than followed without the headers — if the API
    moves, that should be a visible error, not a silent half-request.
    """

    @staticmethod
    def _endpoint(url):
        u = urllib.parse.urlsplit(url)
        port = u.port or (443 if u.scheme == "https" else 80)
        return u.scheme, (u.hostname or "").lower(), port

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old_scheme, old_host, old_port = self._endpoint(req.full_url)
        new_scheme, new_host, new_port = self._endpoint(newurl)
        # Host or port change → a different server, however similar the name looks.
        if (old_host, old_port) != (new_host, new_port):
            raise KaerisError(
                f"Refusing to follow a redirect from {old_host}:{old_port} to "
                f"{new_host}:{new_port} — your API key would be sent to a different server. "
                f"Check --api-url."
            )
        # https → http on the same host is worse than a different host: the key would go out
        # in clear text on the wire.
        if old_scheme == "https" and new_scheme != "https":
            raise KaerisError(
                "Refusing to follow a redirect from https to http — your API key would "
                "travel unencrypted."
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_SameHostRedirect)


def _urlopen(req, timeout):
    """All requests go through the opener above, so no call path can miss the guard."""
    return _opener.open(req, timeout=timeout)


class KaerisClient:
    def __init__(self, api_url=DEFAULT_API, api_key=None, openrouter_key=None, timeout=180):
        self.api_url = checked_api_url(api_url)
        self.api_key = checked_key(api_key, "KAERIS API key")
        self.openrouter_key = checked_key(openrouter_key, "OpenRouter key")
        self.timeout = timeout
        # Set by submit(): ownership proof for the job this client just started.
        self.last_edit_token = ""

    # ── low-level ────────────────────────────────────────────────────────────
    def _headers(self, extra=None):
        h = {"User-Agent": "kaeris-cli"}
        if self.api_key:
            h["X-API-Key"] = self.api_key
        if self.openrouter_key:
            h["X-OpenRouter-Key"] = self.openrouter_key
        if extra:
            h.update(extra)
        return h

    def _get(self, path, extra_headers=None):
        req = urllib.request.Request(self.api_url + path,
                                     headers=self._headers(extra_headers))
        try:
            with _urlopen(req, timeout=self.timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            raise KaerisError(self._err_message(e))
        except urllib.error.URLError as e:
            raise KaerisError(f"Cannot reach {self.api_url}: {e.reason}")

    @staticmethod
    def _err_message(e):
        try:
            body = json.loads(e.read().decode())
            detail = body.get("detail") or body.get("error") or str(body)
        except Exception:
            detail = e.reason
        return f"HTTP {e.code}: {detail}"

    # ── public API ───────────────────────────────────────────────────────────
    def languages(self):
        return json.loads(self._get("/api/languages").decode())

    def config(self):
        return json.loads(self._get("/api/config").decode())

    def key_info(self):
        """Tier, allowance and model for the configured API key (anonymous limits without one).
        Includes `model_id` — the exact model this tier translates with, which the lock records
        so a tier change (and the model swap it brings) is caught as a settings change."""
        return json.loads(self._get("/api/key/info").decode())

    def _multipart(self, filename, content, languages, glossary=None,
                   verify=False, back_lang="en", tone="", icu=False, reuse=None,
                   app_context=""):
        boundary = "----kaeris" + uuid.uuid4().hex
        crlf = b"\r\n"
        parts = []
        parts.append(b"--" + boundary.encode())
        parts.append(
            b'Content-Disposition: form-data; name="file"; filename="'
            + filename.encode() + b'"'
        )
        parts.append(b"Content-Type: application/octet-stream")
        parts.append(b"")
        parts.append(content if isinstance(content, bytes) else content.encode())
        parts.append(b"--" + boundary.encode())
        parts.append(b'Content-Disposition: form-data; name="languages"')
        parts.append(b"")
        parts.append(",".join(languages).encode())
        if glossary:
            parts.append(b"--" + boundary.encode())
            parts.append(b'Content-Disposition: form-data; name="glossary"')
            parts.append(b"")
            parts.append(",".join(glossary).encode())
        if verify:
            parts.append(b"--" + boundary.encode())
            parts.append(b'Content-Disposition: form-data; name="verify"')
            parts.append(b"")
            parts.append(b"1")
            parts.append(b"--" + boundary.encode())
            parts.append(b'Content-Disposition: form-data; name="back_lang"')
            parts.append(b"")
            parts.append((back_lang or "en").encode())
        if tone:
            parts.append(b"--" + boundary.encode())
            parts.append(b'Content-Disposition: form-data; name="tone"')
            parts.append(b"")
            parts.append(tone.encode())
        if icu:
            parts.append(b"--" + boundary.encode())
            parts.append(b'Content-Disposition: form-data; name="icu"')
            parts.append(b"")
            parts.append(b"true")
        if app_context:
            parts.append(b"--" + boundary.encode())
            parts.append(b'Content-Disposition: form-data; name="app_context"')
            parts.append(b"")
            parts.append(app_context.encode())
        if reuse:
            parts.append(b"--" + boundary.encode())
            parts.append(b'Content-Disposition: form-data; name="reuse"')
            parts.append(b"")
            parts.append(json.dumps(reuse, ensure_ascii=False).encode())
        parts.append(b"--" + boundary.encode() + b"--")
        parts.append(b"")
        body = crlf.join(parts)
        return body, "multipart/form-data; boundary=" + boundary

    def submit(self, filename, content, languages, glossary=None,
               verify=False, back_lang="en", tone="", icu=False, reuse=None,
               app_context=""):
        """POST a file for translation; returns job_id.

        tone: "" (neutral, default) / "formal" / "casual".
        app_context: a short free-text description of the app ("a bank app for teenagers"),
             passed to the model so it picks the right sense of ambiguous strings. The API
             truncates it to 300 characters.
        icu: True to hint the model that values may contain ICU MessageFormat
             (plurals/select) so it preserves the syntax.
        reuse: optional {lang: {key: previous_translation}} translation-memory map;
               the server reuses unchanged strings verbatim and only translates
               new/changed ones. Not currently populated by the CLI's --only-new
               (which instead diffs client-side and submits a smaller subset).
        """
        body, ctype = self._multipart(filename, content, languages, glossary,
                                      verify=verify, back_lang=back_lang,
                                      tone=tone, icu=icu, reuse=reuse,
                                      app_context=app_context)
        req = urllib.request.Request(
            self.api_url + "/api/translate", data=body, method="POST",
            headers=self._headers({"Content-Type": ctype}),
        )
        try:
            with _urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            raise KaerisError(self._err_message(e))
        except urllib.error.URLError as e:
            raise KaerisError(f"Cannot reach {self.api_url}: {e.reason}")
        job_id = data.get("job_id")
        if not job_id:
            raise KaerisError(f"No job_id in response: {data}")
        # Proof that WE are the ones who started this job. The server hands it out once, in
        # this response, and asks for it back on the owner-only half of the receipt (plan,
        # spend, app context, glossary). Dropping it on the floor — which is what this client
        # used to do — turned the person who paid for the run into a stranger to it.
        self.last_edit_token = data.get("edit_token") or ""
        return job_id

    def parse(self, filename, content):
        """Parse a locale file into its flat {key: value} map via /api/parse — no translation,
        no cost, and it understands EVERY supported format, not just JSON. Used by the repo-native
        health checks so a non-JSON source (.arb/.strings/.po/.xml/.ftl/…) can be diffed too."""
        boundary = "----kaeris" + uuid.uuid4().hex
        crlf = b"\r\n"
        if not isinstance(content, bytes):
            content = content.encode()
        body = crlf.join([
            b"--" + boundary.encode(),
            b'Content-Disposition: form-data; name="file"; filename="' + filename.encode() + b'"',
            b"Content-Type: application/octet-stream",
            b"",
            content,
            b"--" + boundary.encode() + b"--",
            b"",
        ])
        req = urllib.request.Request(
            self.api_url + "/api/parse", data=body, method="POST",
            headers=self._headers({"Content-Type": "multipart/form-data; boundary=" + boundary}),
        )
        try:
            with _urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            raise KaerisError(self._err_message(e))
        except urllib.error.URLError as e:
            raise KaerisError(f"Cannot reach {self.api_url}: {e.reason}")
        return data.get("keys", {})

    def poll(self, job_id, on_progress=None, interval=1.0, max_wait=1800):
        """Poll a job until done/error. Returns the final status dict."""
        deadline = time.time() + max_wait
        while time.time() < deadline:
            status = json.loads(self._get(f"/api/status/{job_id}").decode())
            if on_progress:
                on_progress(status)
            state = status.get("status")
            if state == "done":
                return status
            if state == "error":
                raise KaerisError(status.get("error") or "Translation failed")
            time.sleep(interval)
        raise KaerisError("Timed out waiting for translation")

    def receipt(self, job_id, edit_token=None):
        """What the server says actually happened on a run: model, plan, languages delivered
        and failed, characters metered against characters reused, the settings applied, which
        glossary terms were really in the source, what QA found. Counts only — no strings.

        The plan, the spend, the app context and the glossary belong to whoever started the
        job, so they come back only when we present its edit_token (kept from submit()).
        Without it the server still answers — with the run's public half — because the same
        endpoint backs the read-only share link."""
        token = edit_token if edit_token is not None else getattr(self, "last_edit_token", "")
        headers = {"X-Job-Token": token} if token else None
        return json.loads(self._get(f"/api/receipt/{job_id}", headers).decode())

    def preview(self, job_id):
        """Fetch the translation QA report: keys _warnings (lost placeholders per lang),
        _qa (UI-overflow risks per lang) and _back (back-translations, if verify was on)."""
        return json.loads(self._get(f"/api/preview/{job_id}").decode())

    def download(self, job_id):
        """Download the result ZIP; returns {member_name: bytes}.

        Member names are checked before they leave this method. Our own server builds them
        from a language code it has already matched against a whitelist — but that is the
        SERVER's half of the guard, and the client's half was missing entirely: whatever the
        archive said, we joined onto the output directory and wrote. Proven 16.08.2026 with a
        stand-in server (which is exactly where KAERIS_API_URL can point): an archive claiming
        `../../.ssh/authorized_keys` and `../../../.bashrc` put both files on disk outside the
        project. A tool an agent drives must not be able to write outside the repo it was
        pointed at, whoever is on the other end of the connection.

        Allowed: `<lang>.<ext>` and one directory level (`values-de/strings.xml` — Android is
        the only nested member we produce). Refused loudly: absolute paths, drive letters,
        `..` in any segment, deeper nesting. A refusal is not partial — an archive that
        contains one of these is not our archive, so nothing from it is written.
        """
        raw = self._get(f"/api/download/{job_id}")
        out = {}
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            for name in zf.namelist():
                if name.endswith(("/", "\\")):
                    continue                       # directory entry — nothing to write
                out[safe_member_name(name)] = zf.read(name)
        return out
