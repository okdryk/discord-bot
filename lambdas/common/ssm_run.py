"""SSM Run Command でパルワールド用EC2上のシェルコマンドを実行する。"""
import logging
import shlex
import time

import boto3

from common import config

logger = logging.getLogger(__name__)

_ssm = None


def ssm_client():
    global _ssm
    if _ssm is None:
        _ssm = boto3.client("ssm")
    return _ssm


class SsmRunError(Exception):
    pass


def run_shell(commands: list[str], timeout_seconds: int = 60) -> str:
    """EC2上でシェルコマンドを実行し、stdoutを返す。失敗時は SsmRunError。

    AWS-RunShellScript は内部的に /bin/sh(dash) でコマンドを実行するため、
    呼び出し側が `set -o pipefail` のようなbash専用構文を使えるように、
    スクリプト全体を bash -c でラップして単一コマンドとして渡す。
    """
    client = ssm_client()
    script = "\n".join(commands)
    wrapped_commands = ["bash -c " + shlex.quote(script)]

    response = client.send_command(
        InstanceIds=[config.INSTANCE_ID],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": wrapped_commands, "executionTimeout": [str(timeout_seconds)]},
    )
    command_id = response["Command"]["CommandId"]

    deadline = time.monotonic() + timeout_seconds + 30
    while time.monotonic() < deadline:
        time.sleep(2)
        try:
            invocation = client.get_command_invocation(
                CommandId=command_id, InstanceId=config.INSTANCE_ID
            )
        except client.exceptions.InvocationDoesNotExist:
            # send_command直後は登録前のことがある
            continue
        status = invocation["Status"]
        if status in ("Pending", "InProgress", "Delayed"):
            continue
        if status == "Success":
            return invocation.get("StandardOutputContent", "")
        raise SsmRunError(
            f"SSMコマンドが{status}: "
            f"{invocation.get('StandardErrorContent', '')[:500] or invocation.get('StandardOutputContent', '')[:500]}"
        )
    raise SsmRunError(f"SSMコマンドが{timeout_seconds}秒以内に完了しませんでした")