import json
import hashlib
import hmac
import base64
import uuid
import time
from urllib.parse import urlparse, parse_qsl, urlencode
import os

# ✅ Secure this in AWS Lambda via Environment Variables
USER_ID = os.environ['USER_ID']
API_KEY = os.environ['API_KEY']

# 🔁 Fix: Normalize URL like Swagger JS does
def normalize_url(url):
    url = url.replace("https://", "").replace("http://", "")
    parsed = urlparse("https://" + url)  # Add scheme for parsing

    host = parsed.netloc.lower()
    path = parsed.path.lower()

    # ✅ Keep original case of query param keys, but sort them
    # sorted_qs = sorted(parse_qsl(parsed.query))
    parsed_qs = parse_qsl(parsed.query)
    query_string = urlencode(parsed_qs)

    if query_string:
        return f"{host}{path}?{query_string}"
    else:
        return f"{host}{path}"

def lambda_handler(event, context=None):
    try:
        body = json.loads(event['body']) if 'body' in event else event

        method = body.get("method", "GET")
        url = body.get("url")
        req_body = body.get("body", "")

        if not url:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Missing url"})
            }

        # 🔧 Use updated normalization logic
        normalized_url = normalize_url(url)

        current_timestamp = int(time.time())
        nonce = str(uuid.uuid4())
        body_md5 = hashlib.md5(req_body.encode("utf-8")).hexdigest().lower() if req_body else ""

        message = USER_ID + str(current_timestamp) + normalized_url + method.lower() + nonce + body_md5

        signature = base64.b64encode(
            hmac.new(API_KEY.encode("utf-8"), message.encode("utf-8"), hashlib.sha512).digest()
        ).decode("utf-8")

        return {
            "statusCode": 200,
            "body": json.dumps({
                "headers": {
                    "UserId": USER_ID,
                    "CurrentTimestamp": current_timestamp,
                    "Authorization": f"Signature {signature}",
                    "Nonce": nonce
                },
                "debug": {
                    "signedMessage": message,
                    "url": normalized_url,
                    "method": method,
                    "bodyMD5": body_md5
                }
            })
        }

    except Exception as e:
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }
