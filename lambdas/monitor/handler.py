"""毎分実行される監視Lambda。

- EC2停止中は即return(スケジュールのON/OFF切替はせず、常時実行で状態不整合を避ける)
- プレイヤー0人が EMPTY_MINUTES_TO_STOP 分続いたら worker を auto_stop でInvoke
  (毎分のカウントではなく「0人になった時刻」との差分で判定し、実行取りこぼしに強くする)
- メモリ使用率が閾値超過ならWebhookで警告(クールダウン付き)
"""
import json
import logging
from datetime import datetime, timedelta, timezone

import boto3

from common import config, discord_api, ec2_control, metrics, palworld_api, state

logger = logging.getLogger(__name__)
logging.getLogger().setLevel(logging.INFO)

_lambda = None


def lambda_client():
    global _lambda
    if _lambda is None:
        _lambda = boto3.client("lambda")
    return _lambda


def handler(event, context):
    if ec2_control.describe()["state"] != "running":
        return

    now = datetime.now(timezone.utc)
    check_auto_stop(now)
    check_memory(now)


def check_auto_stop(now: datetime) -> None:
    if not state.is_auto_stop_enabled():
        return
    try:
        player_count = len(palworld_api.get_players())
    except Exception:
        logger.info("REST API未応答のため今回の自動停止判定をスキップ")
        return

    action = evaluate_auto_stop(
        now, state.get_empty_since(), player_count, config.EMPTY_MINUTES_TO_STOP
    )
    if action == "reset":
        state.set_empty_since(None)
    elif action == "mark":
        state.set_empty_since(now)
    elif action == "stop":
        state.set_empty_since(None)
        lambda_client().invoke(
            FunctionName=config.WORKER_FUNCTION_NAME,
            InvocationType="Event",
            Payload=json.dumps({"kind": "auto_stop"}).encode(),
        )


def evaluate_auto_stop(
    now: datetime,
    empty_since: datetime | None,
    player_count: int,
    threshold_minutes: int,
) -> str:
    """自動停止判定の純粋ロジック。"reset" | "mark" | "stop" | "none" を返す。"""
    if player_count > 0:
        return "reset" if empty_since is not None else "none"
    if empty_since is None:
        return "mark"
    if now - empty_since >= timedelta(minutes=threshold_minutes):
        return "stop"
    return "none"


def check_memory(now: datetime) -> None:
    memory = metrics.get_memory_used_percent()
    if memory is None or memory <= config.MEMORY_ALERT_PERCENT:
        return
    last_alert = state.get_last_mem_alert()
    if last_alert is not None and now - last_alert < timedelta(
        minutes=config.MEMORY_ALERT_COOLDOWN_MINUTES
    ):
        return
    discord_api.post_channel_webhook(
        f"⚠️ メモリ使用率が {memory:.1f}% です。`/palworld restart` の実行を検討してください"
    )
    state.set_last_mem_alert(now)
