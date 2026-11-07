import base64
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .settings import Held, Settings, Unavailable, origin
from .torrent import MAX_METAINFO


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Http:
    def __init__(self):
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(self, url, *, method="GET", headers=None, data=None, limit=8 * 1024**2):
        req = Request(url, data=data, headers=headers or {}, method=method)
        try:
            with self.opener.open(req, timeout=60) as response:
                body = response.read(limit + 1)
                if len(body) > limit:
                    raise Held("http_response_size_limit")
                return body
        except HTTPError:
            raise
        except (URLError, TimeoutError, OSError) as exc:
            raise Held("http_request_failed") from exc


class Sonarr:
    def __init__(self, settings: Settings, http=None):
        self.settings = settings
        self.http = http or Http()

    def call(self, path, *, params=None, data=None, method=None):
        if (data is not None or method not in {None, "GET"}) and self.settings.dry_run:
            raise Held("dry_run_blocks_sonarr_mutation")
        url = self.settings.sonarr_url.rstrip("/") + "/api/v3/" + path
        if params:
            url += "?" + urlencode(params)
        try:
            body = self.http.request(url, method=method or ("POST" if data is not None else "GET"),
                                     headers={"X-Api-Key": self.settings.sonarr_api_key, "Content-Type": "application/json"},
                                     data=json.dumps(data).encode() if data is not None else None)
            return json.loads(body) if body else None
        except HTTPError as exc:
            if data is None and method in {None, "GET"} and exc.code >= 500:
                raise Unavailable("sonarr_temporarily_unavailable") from exc
            raise Held(f"sonarr_http_{exc.code}") from exc
        except Held as exc:
            if data is None and method in {None, "GET"} and not isinstance(exc, Unavailable):
                raise Unavailable("sonarr_read_unavailable") from exc
            raise
        except (ValueError, UnicodeError) as exc:
            raise Held("invalid_sonarr_response") from exc

    def metainfo(self, release, indexer):
        fields = {f["name"]: f.get("value") for f in indexer.get("fields", [])}
        url = release.get("downloadUrl", "")
        if not fields.get("baseUrl") or origin(url) != origin(fields["baseUrl"]):
            raise Held("torrent_origin_does_not_match_indexer")
        try:
            return self.http.request(url, limit=MAX_METAINFO)
        except HTTPError as exc:
            raise Held(f"metainfo_http_{exc.code}") from exc


class Transmission:
    READ_METHODS = {"session-get", "torrent-get"}
    FIELDS = ["hashString", "name", "downloadDir", "labels", "files", "fileStats", "status", "error", "metadataPercentComplete"]

    def __init__(self, settings: Settings, http=None):
        self.settings = settings
        self.http = http or Http()
        self.session = ""

    def call(self, method, arguments=None):
        if self.settings.dry_run and method not in self.READ_METHODS:
            raise Held("dry_run_blocks_transmission_mutation")
        auth = self.settings.transmission_username + ":" + self.settings.transmission_password
        headers = {"Content-Type": "application/json", "Authorization": "Basic " + base64.b64encode(auth.encode()).decode()}
        payload = json.dumps({"method": method, "arguments": arguments or {}}).encode()
        # A 409 explicitly means the operation was not accepted; timeouts are never replayed.
        for _ in range(2):
            headers["X-Transmission-Session-Id"] = self.session
            try:
                body = self.http.request(self.settings.transmission_url, method="POST", headers=headers, data=payload)
                response = json.loads(body)
                if response.get("result") != "success":
                    raise Held("transmission_rpc_rejected")
                return response["arguments"]
            except HTTPError as exc:
                if exc.code == 409 and exc.headers.get("X-Transmission-Session-Id"):
                    self.session = exc.headers["X-Transmission-Session-Id"]
                    continue
                if method in self.READ_METHODS and exc.code >= 500:
                    raise Unavailable("transmission_temporarily_unavailable") from exc
                raise Held(f"transmission_http_{exc.code}") from exc
            except Held as exc:
                if method in self.READ_METHODS and not isinstance(exc, Unavailable):
                    raise Unavailable("transmission_read_unavailable") from exc
                raise
            except (ValueError, KeyError, UnicodeError) as exc:
                raise Held("invalid_transmission_response") from exc
        raise Held("transmission_session_failed")

    def get(self, info_hash):
        rows = self.call("torrent-get", {"ids": [info_hash], "fields": self.FIELDS}).get("torrents", [])
        if len(rows) > 1:
            raise Held("ambiguous_transmission_hash")
        return rows[0] if rows else None
