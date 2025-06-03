import boto3, json

REGION = "ap-south-1"
LAMBDA_NAME = "AuthSignatureLambda"
lambda_client = boto3.client("lambda", region_name=REGION)

def get_signature_headers(api_url, method, body_dict):
    payload = {
        "method": method,
        "url": api_url,
        "body": json.dumps(body_dict) if body_dict else ""
    }
    event = {"body": json.dumps(payload)}

    res = lambda_client.invoke(
        FunctionName=LAMBDA_NAME,
        InvocationType='RequestResponse',
        Payload=json.dumps(event)
    )
    body = json.loads(res['Payload'].read())
    headers = json.loads(body['body']).get('headers', {})
    
    # ✅ Ensure all header values are strings
    return {k: str(v) for k, v in headers.items()}
