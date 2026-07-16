"""Zero-dependency API client for the KAERIS i18n service (stdlib only)."""

import io
import json
import os
import time
import uuid
import zipfile
import urllib.request
import urllib.error

DEFAULT_API = "https://kaeris.dev"


class KaerisError(Exception):
    """Any error talking to the KAERIS API."""


class KaerisClient:
    def __init__(self, api_url=DEFAULT_API, api_key=None, openrouter_key=None, timeout=180):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.openrouter_key = openrouter_key
        self.timeout = timeout

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

    def _get(self, path):
        req = urllib.request.Request(self.api_url + path, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
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

    def _multipart(self, filename, content, languages, glossary=None,
                   verify=False, back_lang="en", tone="", icu=False, reuse=None):
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
               verify=False, back_lang="en", tone="", icu=False, reuse=None):
        """POST a file for translation; returns job_id.

        tone: "" (neutral, default) / "formal" / "casual".
        icu: True to hint the model that values may contain ICU MessageFormat
             (plurals/select) so it preserves the syntax.
        reuse: optional {lang: {key: previous_translation}} translation-memory map;
               the server reuses unchanged strings verbatim and only translates
               new/changed ones. Not currently populated by the CLI's --only-new
               (which instead diffs client-side and submits a smaller subset).
        """
        body, ctype = self._multipart(filename, content, languages, glossary,
                                      verify=verify, back_lang=back_lang,
                                      tone=tone, icu=icu, reuse=reuse)
        req = urllib.request.Request(
            self.api_url + "/api/translate", data=body, method="POST",
            headers=self._headers({"Content-Type": ctype}),
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            raise KaerisError(self._err_message(e))
        except urllib.error.URLError as e:
            raise KaerisError(f"Cannot reach {self.api_url}: {e.reason}")
        job_id = data.get("job_id")
        if not job_id:
            raise KaerisError(f"No job_id in response: {data}")
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
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
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

    def preview(self, job_id):
        """Fetch the translation QA report: keys _warnings (lost placeholders per lang),
        _qa (UI-overflow risks per lang) and _back (back-translations, if verify was on)."""
        return json.loads(self._get(f"/api/preview/{job_id}").decode())

    def download(self, job_id):
        """Download the result ZIP; returns {member_name: bytes}."""
        raw = self._get(f"/api/download/{job_id}")
        out = {}
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            for name in zf.namelist():
                out[name] = zf.read(name)
        return out
