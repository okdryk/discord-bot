"""Discord Interactions Endpoint の受付Lambda。

署名検証(Ed25519)とPING応答、deferred応答(type 5)の返却だけを行い、
実処理は worker Lambda に非同期Invokeで委譲する(Discordの3秒応答制限のため)。
"""
import base64
import json
import logging
import os

import boto3
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from common import config

logger = logging.getLogger(__name__)
logging.getLogger().setLevel(logging.INFO)

_lambda = None
_public_key: str | None = None

# Interaction types / callback types
# https://discord.com/developers/docs/interactions/receiving-and-responding
PING = 1
APPLICATION_COMMAND = 2
PONG = 1
DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE = 5


def lambda_client():
    global _lambda
    if _lambda is None:
        _lambda = boto3.client("lambda")
    return _lambda


def get_public_key() -> str:
    global _public_key
    if _public_key is None:
        _public_key = os.environ["DISCORD_PUBLIC_KEY"]
    return _public_key


def verify_signature(raw_body: bytes, signature: str, timestamp: str, public_key_hex: str) -> bool:
    try:
        verify_key = VerifyKey(bytes.fromhex(public_key_hex))
        verify_key.verify(timestamp.encode() + raw_body, bytes.fromhex(signature))
        return True
    except (BadSignatureError, ValueError):
        return False


def _json_response(status: int, payload: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload),
    }


def handler(event, context):
    # 署名はraw bodyに対して検証する必要があるため、パース前にデコードする
    body = event.get("body") or ""
    raw_body = base64.b64decode(body) if event.get("isBase64Encoded") else body.encode()

    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    signature = headers.get("x-signature-ed25519")
    timestamp = headers.get("x-signature-timestamp")
    if (
        not signature
        or not timestamp
        or not verify_signature(raw_body, signature, timestamp, get_public_key())
    ):
        return {"statusCode": 401, "body": "invalid request signature"}

    interaction = json.loads(raw_body)

    if interaction["type"] == PING:
        return _json_response(200, {"type": PONG})

    if interaction["type"] == APPLICATION_COMMAND:
        payload = {
            "kind": "command",
            "application_id": interaction["application_id"],
            "interaction_token": interaction["token"],
            "data": interaction["data"],
        }
        lambda_client().invoke(
            FunctionName=config.WORKER_FUNCTION_NAME,
            InvocationType="Event",
            Payload=json.dumps(payload).encode(),
        )
        return _json_response(200, {"type": DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE})

    logger.warning("unsupported interaction type: %s", interaction["type"])
    return {"statusCode": 400, "body": "unsupported interaction type"}
