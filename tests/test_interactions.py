import base64
import json

import pytest
from nacl.signing import SigningKey

from interactions import handler as interactions


@pytest.fixture()
def keypair():
    signing_key = SigningKey.generate()
    return signing_key, signing_key.verify_key.encode().hex()


def sign(signing_key: SigningKey, timestamp: str, body: bytes) -> str:
    return signing_key.sign(timestamp.encode() + body).signature.hex()


def make_event(body: bytes, signature: str, timestamp: str, b64: bool = False) -> dict:
    return {
        "body": base64.b64encode(body).decode() if b64 else body.decode(),
        "isBase64Encoded": b64,
        "headers": {
            "x-signature-ed25519": signature,
            "x-signature-timestamp": timestamp,
        },
    }


def test_verify_signature_valid(keypair):
    signing_key, public_key = keypair
    body = b'{"type":1}'
    signature = sign(signing_key, "1720000000", body)
    assert interactions.verify_signature(body, signature, "1720000000", public_key)


def test_verify_signature_invalid(keypair):
    _, public_key = keypair
    other_key = SigningKey.generate()
    body = b'{"type":1}'
    signature = sign(other_key, "1720000000", body)
    assert not interactions.verify_signature(body, signature, "1720000000", public_key)


def test_verify_signature_tampered_body(keypair):
    signing_key, public_key = keypair
    signature = sign(signing_key, "1720000000", b'{"type":1}')
    assert not interactions.verify_signature(
        b'{"type":2}', signature, "1720000000", public_key
    )


def test_handler_ping_returns_pong(keypair, monkeypatch):
    signing_key, public_key = keypair
    monkeypatch.setattr(interactions, "get_public_key", lambda: public_key)
    body = json.dumps({"type": 1}).encode()
    event = make_event(body, sign(signing_key, "123", body), "123")

    response = interactions.handler(event, None)
    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {"type": 1}


def test_handler_ping_base64_body(keypair, monkeypatch):
    signing_key, public_key = keypair
    monkeypatch.setattr(interactions, "get_public_key", lambda: public_key)
    body = json.dumps({"type": 1}).encode()
    event = make_event(body, sign(signing_key, "123", body), "123", b64=True)

    response = interactions.handler(event, None)
    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {"type": 1}


def test_handler_bad_signature_returns_401(keypair, monkeypatch):
    _, public_key = keypair
    other_key = SigningKey.generate()
    monkeypatch.setattr(interactions, "get_public_key", lambda: public_key)
    body = json.dumps({"type": 1}).encode()
    event = make_event(body, sign(other_key, "123", body), "123")

    response = interactions.handler(event, None)
    assert response["statusCode"] == 401


def test_handler_missing_headers_returns_401(keypair, monkeypatch):
    _, public_key = keypair
    monkeypatch.setattr(interactions, "get_public_key", lambda: public_key)
    event = {"body": '{"type":1}', "headers": {}}

    response = interactions.handler(event, None)
    assert response["statusCode"] == 401


def test_handler_command_invokes_worker_and_defers(keypair, monkeypatch):
    signing_key, public_key = keypair
    monkeypatch.setattr(interactions, "get_public_key", lambda: public_key)

    invoked = {}

    class FakeLambda:
        def invoke(self, **kwargs):
            invoked.update(kwargs)

    monkeypatch.setattr(interactions, "lambda_client", lambda: FakeLambda())

    body = json.dumps(
        {
            "type": 2,
            "application_id": "app123",
            "token": "tok456",
            "data": {"name": "palworld", "options": [{"name": "status"}]},
        }
    ).encode()
    event = make_event(body, sign(signing_key, "123", body), "123")

    response = interactions.handler(event, None)
    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {"type": 5}

    payload = json.loads(invoked["Payload"])
    assert invoked["InvocationType"] == "Event"
    assert payload["kind"] == "command"
    assert payload["application_id"] == "app123"
    assert payload["interaction_token"] == "tok456"
