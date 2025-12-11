import time
import uuid
import hmac
import hashlib
import base64
import json
from urllib.parse import urlencode
from dotenv import load_dotenv
import os

load_dotenv()

# ===== configure these from env / config =====
USER_ID_basic = os.getenv("USER_ID_basic")
API_KEY_basic = os.getenv("API_KEY_basic")                   # example; replace
# =============================================

def _strip_scheme_and_lower(url: str) -> str:
    """Remove http(s) scheme if present and lowercase the whole remaining string."""
    if url.startswith("https://"):
        url = url[len("https://"):]
    elif url.startswith("http://"):
        url = url[len("http://"):]
    return url.lower()

def _raw_body_from_body_param(body):
    """
    Convert body parameter into the raw string that Node would have had as req.body.
    Node used the literal req.body string. To mimic that:
    - if body is None/"" -> return ""
    - if body is str -> return as-is
    - if body is bytes -> decode utf-8
    - if body is dict/list -> produce compact JSON WITHOUT sorting keys
      (js clients usually stringify with default key order)
    """
    if body is None:
        return ""
    if isinstance(body, bytes):
        return body.decode("utf-8")
    if isinstance(body, str):
        return body
    # dict/list/other -> compact JSON (no spaces). Do NOT sort keys (to mimic JS JSON.stringify)
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False)

def get_signature_headers(full_url: str, method: str, body=None):
    """
    Create headers matching the Node/swagger interceptor.

    Args:
      full_url: Full request URL including query string if any (e.g. "https://host/path?a=1&b=2")
      method: HTTP method string, e.g. "POST"
      body: raw request body string OR bytes OR dict/list. For query-param calls pass body=None or "".

    Returns:
      dict with keys: UserId, CurrentTimestamp, Authorization, Nonce
    """
    # normalized url like Node: remove scheme and lowercase full remainder
    normalized_url = _strip_scheme_and_lower(full_url)

    # timestamp and nonce (Node used seconds)
    current_timestamp = str(int(time.time()))
    nonce = str(uuid.uuid4())

    # body raw and md5 lower-hex (empty string if no body)
    raw_body = _raw_body_from_body_param(body)
    if raw_body:
        body_md5 = hashlib.md5(raw_body.encode("utf-8")).hexdigest().lower()
    else:
        body_md5 = ""

    # message composition exactly as Node
    message = f"{USER_ID_basic}{current_timestamp}{normalized_url}{method.lower()}{nonce}{body_md5}"

    # HMAC-SHA512 using API_KEY (TEXT), then base64
    mac = hmac.new(API_KEY_basic.encode("utf-8"), message.encode("utf-8"), hashlib.sha512).digest()
    signature_b64 = base64.b64encode(mac).decode("utf-8")

    headers = {
        "UserId": USER_ID_basic,
        "CurrentTimestamp": current_timestamp,
        "Authorization": f"Signature {signature_b64}",
        "Nonce": nonce
    }

    return headers
