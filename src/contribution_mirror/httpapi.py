"""A small JSON-over-HTTPS client shared by all providers: keep-alive per thread, retries on rate limits and 5xx."""
import http.client
import json
import threading
import time
from urllib.parse import urlencode, urlsplit

LOOPBACK = ("localhost", "127.0.0.1", "::1")


class ApiError(RuntimeError):
    """The server answered, but not with success. `status` is the HTTP status."""
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class Api:
    """GET-only client for one API base URL with fixed headers (the provider decides how a token is sent).
    Tokens only ever travel over https; plain http is accepted for loopback addresses (tests, local mirrors)."""
    def __init__(self, base_url, headers=None):
        base = urlsplit(base_url)
        self.scheme = base.scheme or "https"
        self.host, self.prefix = base.netloc, base.path.rstrip("/")
        if self.scheme != "https" and not (self.scheme == "http" and (base.hostname or "") in LOOPBACK):
            raise ValueError(f"refusing to send credentials over {self.scheme}:// to {self.host}")
        self.headers = {"Accept": "application/json", "User-Agent": "contribution-mirror", **(headers or {})}
        self.local = threading.local()

    def _connect(self):
        cls = http.client.HTTPSConnection if self.scheme == "https" else http.client.HTTPConnection
        return cls(self.host, timeout=30)

    def _target(self, path, params):
        if path.startswith(("http://", "https://")):      # absolute link from a "next page" field
            u = urlsplit(path)
            if u.netloc != self.host:
                raise ValueError(f"refusing to follow a link to another host: {u.netloc}")
            return u.path + (f"?{u.query}" if u.query else "")
        return self.prefix + path + ("?" + urlencode(params, doseq=True) if params else "")

    def get(self, path, **params):
        """Decoded JSON of a successful GET. Raises ApiError for any other final status."""
        return self.get_with_headers(path, **params)[0]

    def get_with_headers(self, path, **params):
        """(decoded JSON, response headers as a lowercase dict)."""
        url = self._target(path, params)
        for attempt in range(6):
            try:
                if getattr(self.local, "conn", None) is None:
                    self.local.conn = self._connect()
                self.local.conn.request("GET", url, headers=self.headers)
                r = self.local.conn.getresponse()
                body = r.read()
            except (OSError, http.client.HTTPException):
                if getattr(self.local, "conn", None) is not None:
                    self.local.conn.close()     # release the socket; reconnect on the next attempt
                self.local.conn = None
                time.sleep(min(2 ** attempt, 20))
                continue
            headers = {k.lower(): v for k, v in r.getheaders()}
            if r.status == 200:
                return (json.loads(body) if body else None), headers
            if r.status in (403, 429) or r.status >= 500:
                wait = headers.get("retry-after")
                reset = headers.get("x-ratelimit-reset")
                if wait is None and headers.get("x-ratelimit-remaining") == "0" and reset:
                    wait = max(0, int(reset) - time.time()) + 1
                if r.status in (403, 429) and wait is None:    # forbidden (e.g. no access), not a rate limit
                    raise ApiError(r.status, f"API {r.status} for {url}: {body[:200]!r}")
                time.sleep(min(float(wait) if wait else 2 ** attempt, 90))
                continue
            raise ApiError(r.status, f"API {r.status} for {url}: {body[:200]!r}")
        raise ApiError(0, f"API: giving up on {url} after retries")

    def pages(self, path, page_param="page", size_param="per_page", size=100, key=None, max_pages=1000, **params):
        """Yield every item of a page-numbered list endpoint (`key` names the list inside an object response)."""
        for page in range(1, max_pages + 1):
            data = self.get(path, **{page_param: page, size_param: size}, **params)
            items = data.get(key, []) if key else data
            yield from items
            if len(items) < size:
                return
