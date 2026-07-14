"""SSM Parameter Store での状態・シークレットの読み書き。"""
from datetime import datetime, timezone

import boto3

from common import config

_ssm = None


def ssm_client():
    global _ssm
    if _ssm is None:
        _ssm = boto3.client("ssm")
    return _ssm


def get_param(name: str, default: str | None = None, decrypt: bool = False) -> str | None:
    client = ssm_client()
    try:
        response = client.get_parameter(Name=name, WithDecryption=decrypt)
    except client.exceptions.ParameterNotFound:
        return default
    return response["Parameter"]["Value"]


def put_param(name: str, value: str) -> None:
    ssm_client().put_parameter(Name=name, Value=value, Type="String", Overwrite=True)


def get_secret(name: str) -> str:
    value = get_param(name, decrypt=True)
    if value is None:
        raise RuntimeError(f"シークレット {name} がParameter Storeに未登録です")
    return value


# --- 監視用の状態 ---

def is_auto_stop_enabled() -> bool:
    return get_param(config.PARAM_AUTO_STOP_ENABLED, default="true") == "true"


def set_auto_stop_enabled(enabled: bool) -> None:
    put_param(config.PARAM_AUTO_STOP_ENABLED, "true" if enabled else "false")


def _get_time_param(name: str) -> datetime | None:
    value = get_param(name, default="none")
    if value == "none":
        return None
    return datetime.fromisoformat(value)


def _set_time_param(name: str, when: datetime | None) -> None:
    put_param(name, when.astimezone(timezone.utc).isoformat() if when else "none")


def get_empty_since() -> datetime | None:
    return _get_time_param(config.PARAM_EMPTY_SINCE)


def set_empty_since(when: datetime | None) -> None:
    _set_time_param(config.PARAM_EMPTY_SINCE, when)


def get_last_mem_alert() -> datetime | None:
    return _get_time_param(config.PARAM_LAST_MEM_ALERT)


def set_last_mem_alert(when: datetime) -> None:
    _set_time_param(config.PARAM_LAST_MEM_ALERT, when)
