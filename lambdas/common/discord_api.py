"""Discord への通知。requestsは使わずboto3同梱のurllib3で済ませる。"""
import json
import logging

import urllib3

from common import config, state

logger = logging.getLogger(__name__)

_http = urllib3.PoolManager()

API_BASE = "https://discord.com/api/v10"


def _request(method: str, url: str, payload: dict) -> None:
    response = _http.request(
        method,
        url,
        body=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"},
    )
    if response.status >= 300:
        logger.warning(
            "Discord API %s %s failed: %s %s",
            method, url.split("/webhooks/")[0], response.status, response.data[:300],
        )


def edit_original_response(application_id: str, interaction_token: str, content: str) -> None:
    """deferred応答(type 5)の「考え中...」を実際の結果で置き換える。"""
    url = f"{API_BASE}/webhooks/{application_id}/{interaction_token}/messages/@original"
    _request("PATCH", url, {"content": content})


def post_channel_webhook(content: str) -> None:
    """コマンド起点でない通知(自動停止・メモリ警告)用のチャンネルWebhook。"""
    url = state.get_secret(config.PARAM_DISCORD_WEBHOOK_URL)
    _request("POST", url, {"content": content})
