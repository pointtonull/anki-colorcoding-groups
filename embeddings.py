"""Text embeddings from any OpenAI-compatible `/embeddings` endpoint, used to suggest group colours (see palette.py).

Anki-free and standard library only. One client covers OpenAI and Ollama (which serves the same API under
`http://localhost:11434/v1`), plus anything else that speaks it (LM Studio, llama.cpp, vLLM, Azure/OpenRouter-style
gateways...).

Nothing here runs on its own: embeddings are requested only when the user asks for colour suggestions, and what is
sent is exactly `group_texts()`: each group's name and a few of its words.
"""
import ipaddress
import json
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request

BATCH_SIZE = 128  # texts per request (OpenAI accepts up to 2048)
MAX_WORDS_PER_GROUP = 8  # sample words sent per group, besides its name
MAX_WORD_CHARS = 40
TIMEOUT_S = 60

PROVIDERS = {
    "openai": {"label": "OpenAI", "baseUrl": "https://api.openai.com/v1", "model": "text-embedding-3-small"},
    "ollama": {"label": "Ollama (local)", "baseUrl": "http://localhost:11434/v1", "model": "nomic-embed-text"},
    "custom": {"label": "Other (OpenAI-compatible)", "baseUrl": "", "model": ""},
}
DEFAULT_PROVIDER = "openai"


class EmbeddingError(Exception):
    """Something the user can act on: a bad URL, a rejected key, a model that is not installed... The message is safe
    to show (it never contains the API key)."""


def normalize_provider_settings(data, environ=None):
    """The provider settings as stored in settings.json, with the missing pieces filled in from the preset. An OpenAI
    key left empty is read from the OPENAI_API_KEY environment variable (Anki started from a terminal sees it)."""
    data = data if isinstance(data, dict) else {}
    provider = str(data.get("provider"))
    if provider not in PROVIDERS:
        provider = DEFAULT_PROVIDER
    preset = PROVIDERS[provider]
    settings = {
        "provider": provider,
        "baseUrl": str(data.get("baseUrl") or preset["baseUrl"]).strip(),
        "model": str(data.get("model") or preset["model"]).strip(),
        "apiKey": str(data.get("apiKey") or "").strip(),
    }
    if not settings["apiKey"] and provider == "openai" and environ:
        settings["apiKey"] = str(environ.get("OPENAI_API_KEY") or "").strip()
    return settings


def is_local_host(hostname):
    if not hostname:
        return False
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def endpoint_url(base_url):
    """`https://host/v1` (or the full `.../embeddings` URL) to the embeddings endpoint, validated."""
    base_url = (base_url or "").strip()
    parts = urllib.parse.urlsplit(base_url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise EmbeddingError(f"“{base_url}” is not a web address. Use something like https://api.openai.com/v1")
    path = parts.path.rstrip("/")
    if not path.endswith("/embeddings"):
        path += "/embeddings"
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def host_of(base_url):
    return urllib.parse.urlsplit((base_url or "").strip()).hostname or ""


def sends_off_this_computer(base_url):
    """True unless the endpoint is on this machine: the user is asked before anything is sent there."""
    return not is_local_host(host_of(base_url))


def _ssl_context():
    try:
        import certifi  # bundled with Anki; the system store may be missing in its Python
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _error_text(raw, api_key):
    try:
        body = json.loads(raw)
        message = body["error"]["message"] if isinstance(body["error"], dict) else body["error"]
    except Exception:
        message = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    message = " ".join(str(message).split())[:300]
    return message.replace(api_key, "***") if api_key else message


def _post(url, payload, api_key, timeout, opener):
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json", "Accept": "application/json"})
    if api_key:  # unredirected: a redirect to another host must not receive the key
        request.add_unredirected_header("Authorization", f"Bearer {api_key}")
    host = host_of(url)
    try:
        with opener(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        try:
            detail = _error_text(error.read(), api_key)
        finally:
            error.close()
        hint = " Check the API key." if error.code in (401, 403) else ""
        hint = hint or (" Check the model name (is it installed or available?)." if error.code == 404 else "")
        if hint and not detail.endswith((".", "!", "?")):
            detail += "."
        raise EmbeddingError(f"{host} answered HTTP {error.code}: {detail}{hint}") from None
    except urllib.error.URLError as error:
        if isinstance(error.reason, socket.timeout):
            raise EmbeddingError(f"{host} did not answer within {timeout} seconds.") from None
        raise EmbeddingError(f"Could not reach {host}: {error.reason}") from None
    except socket.timeout:  # an alias of TimeoutError only from Python 3.10 on
        raise EmbeddingError(f"{host} did not answer within {timeout} seconds.") from None
    except (ValueError, OSError) as error:  # bad JSON, dropped connection...
        raise EmbeddingError(f"{host} sent a response that could not be read ({error}).") from None


def request_embeddings(texts, base_url, model, api_key="", timeout=TIMEOUT_S, opener=None):
    """One embedding vector per text, in order. `opener` replaces urllib's `urlopen` (for tests)."""
    if not texts:
        return []
    if not model:
        raise EmbeddingError("No model is set.")
    url = endpoint_url(base_url)
    if api_key and urllib.parse.urlsplit(url).scheme == "http" and not is_local_host(host_of(url)):
        raise EmbeddingError("Refusing to send an API key over plain http to a remote host. Use https.")
    context = _ssl_context() if opener is None else None

    def open_url(request, timeout):
        if opener is not None:
            return opener(request, timeout=timeout)
        return urllib.request.urlopen(request, timeout=timeout, context=context)

    vectors = []
    for start in range(0, len(texts), BATCH_SIZE):
        batch = texts[start:start + BATCH_SIZE]
        body = _post(url, {"model": model, "input": batch}, api_key, timeout, open_url)
        try:
            data = [item for _, item in sorted(enumerate(body["data"]), key=lambda pair: pair[1].get("index", pair[0]))]
            embeddings = [[float(x) for x in item["embedding"]] for item in data]
        except (KeyError, TypeError, ValueError):
            raise EmbeddingError(f"{host_of(url)} did not return embeddings in the OpenAI format.") from None
        if len(embeddings) != len(batch) or len({len(e) for e in embeddings}) != 1:
            raise EmbeddingError(f"{host_of(url)} returned {len(embeddings)} embeddings for {len(batch)} texts.")
        vectors.extend(embeddings)
    return vectors


def group_texts(groups, entries, max_words=MAX_WORDS_PER_GROUP):
    """[(group name, text sent)], sorted by name so that colours do not depend on the file order. The text is the name
    followed by up to `max_words` of the group's words ("Cities: London, Lisbon"): a name alone can mean little
    ("Reliqs"), the words say what the group is about."""
    words = {name: [] for name in groups}
    for entry in entries:
        group, word = entry.get("group"), str(entry.get("word", "")).strip()
        if group in words and word and word[:MAX_WORD_CHARS] not in words[group] and len(words[group]) < max_words:
            words[group].append(word[:MAX_WORD_CHARS])
    ordered = sorted(groups, key=lambda name: (name.lower(), name))
    return [(name, f"{name}: {', '.join(words[name])}" if words[name] else name) for name in ordered]
