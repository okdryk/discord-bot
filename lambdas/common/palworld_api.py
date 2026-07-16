"""Palworld REST API を SSM Run Command 経由で EC2 上の localhost:8212 に対して呼び出す。

8212ポートを外部公開せずに済ませるための構成。AdminPasswordはEC2側で
Parameter Storeから取得させ、コマンド文字列に平文を含めない。
"""
import json

from common import config, ssm_run


def _curl(path: str, method: str = "GET", body: dict | None = None, timeout: int = 10) -> str:
    script = [
        "set -eu -o pipefail",
        (
            'PW=$(aws ssm get-parameter'
            f' --name "{config.PARAM_ADMIN_PASSWORD}"'
            " --with-decryption --query Parameter.Value --output text"
            f" --region {config.AWS_REGION})"
        ),
    ]
    curl = f'curl -fsS -m {timeout} -u "admin:$PW"'
    if method != "GET":
        curl += f" -X {method}"
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).replace("'", "'\\''")
        curl += f" -H 'Content-Type: application/json' -d '{payload}'"
    curl += f" http://localhost:{config.REST_API_PORT}/v1/api/{path}"
    script.append(curl)
    return ssm_run.run_shell(script, timeout_seconds=timeout + 20)


def get_info() -> dict:
    return json.loads(_curl("info"))


def get_players() -> list[dict]:
    return json.loads(_curl("players")).get("players", [])


def save() -> None:
    _curl("save", method="POST", body={}, timeout=60)


def announce(message: str) -> None:
    _curl("announce", method="POST", body={"message": message})


def shutdown(waittime: int, message: str) -> None:
    _curl("shutdown", method="POST", body={"waittime": waittime, "message": message})
