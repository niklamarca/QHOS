"""HTTP local limitado: sem proxies de ambiente, redirecionamentos ou leitura ilimitada."""
import json
import urllib.request
import urllib.error
from urllib.parse import urlsplit


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise ValueError("local_http_redirect_refused")


def open_local(request, timeout, context=None):
    url = request.full_url if isinstance(request, urllib.request.Request) else request
    p = urlsplit(url)
    if (p.scheme not in {"http", "https"} or p.hostname not in {"127.0.0.1", "localhost", "::1"}
            or p.username or p.password or p.fragment):
        raise ValueError("local_http_url_refused")
    handlers = [urllib.request.ProxyHandler({}), NoRedirect()]
    if context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=context))
    return urllib.request.build_opener(*handlers).open(request, timeout=timeout)


def get_json(url, timeout):
    request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    with open_local(request, timeout) as response:
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError("local_http_body_too_large")
    result = json.loads(raw)
    json.dumps(result, allow_nan=False)
    if not isinstance(result, dict):
        raise ValueError("local_http_not_object")
    return result
