"""Small OpenAI-compatible HTTP client. Credentials never enter output records."""
import json
import urllib.error
import urllib.parse
import urllib.request


def validate_url(base_url):
    u = urllib.parse.urlsplit(base_url)
    if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password or u.query or u.fragment:
        raise ValueError('base URL must be an HTTP(S) API root without credentials, query or fragment')
    return base_url.rstrip('/')


class CompletionError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # A completion redirect must not forward an Authorization header to another host.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def complete(base_url, api_key, payload, timeout=180):
    base_url = validate_url(base_url)
    headers = {'Content-Type': 'application/json', 'User-Agent': 'ASD-Bench/0.1.0'}
    if api_key:
        headers['Authorization'] = 'Bearer ' + api_key
    request = urllib.request.Request(base_url + '/chat/completions',
        data=json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8'), headers=headers)
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise CompletionError('response_size_limit')
            result = json.loads(raw)
    except urllib.error.HTTPError as exc:
        # Providers sometimes echo requests or credentials in errors: do not log their body.
        raise CompletionError(f'http_{exc.code}') from None
    except (urllib.error.URLError, OSError, TimeoutError):
        raise CompletionError('connection_or_timeout') from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise CompletionError('invalid_response_json') from None
    try:
        choice = result['choices'][0]
        if choice['finish_reason'] != 'stop':
            raise CompletionError('completion_not_finished')
        content = choice['message']['content']
        if not isinstance(content, str) or not content.strip():
            raise CompletionError('empty_completion')
        return content
    except (KeyError, TypeError, IndexError):
        raise CompletionError('invalid_completion_envelope') from None
