#!/usr/bin/env python3
"""Discordのguildスラッシュコマンドを登録する(PUTで全置換・冪等)。

usage:
    python scripts/register_commands.py --application-id <APP_ID> --guild-id <GUILD_ID>

Botトークンは Parameter Store の /palworld/secrets/discord_bot_token から取得する
(環境変数 DISCORD_BOT_TOKEN があればそちらを優先)。
guildコマンドはglobalコマンドと違い即時反映される。個人サーバー用途なのでguildで十分。
"""
import argparse
import json
import os
import urllib.request

SUB_COMMAND = 1
OPTION_STRING = 3

COMMANDS = [
    {
        "name": "palworld",
        "description": "パルワールドサーバーの操作",
        "options": [
            {"type": SUB_COMMAND, "name": "start", "description": "サーバーを起動する"},
            {
                "type": SUB_COMMAND,
                "name": "stop",
                "description": "セーブ&S3バックアップしてサーバーを停止する",
            },
            {"type": SUB_COMMAND, "name": "restart", "description": "サーバーを再起動する"},
            {
                "type": SUB_COMMAND,
                "name": "status",
                "description": "EC2状態・プレイヤー一覧・メモリ使用率を表示する",
            },
            {
                "type": SUB_COMMAND,
                "name": "backup",
                "description": "停止せずセーブデータをS3にバックアップする",
            },
            {
                "type": SUB_COMMAND,
                "name": "autostop",
                "description": "無人時の自動停止のON/OFFを切り替える",
                "options": [
                    {
                        "type": OPTION_STRING,
                        "name": "mode",
                        "description": "on または off",
                        "required": True,
                        "choices": [
                            {"name": "on", "value": "on"},
                            {"name": "off", "value": "off"},
                        ],
                    }
                ],
            },
        ],
    }
]


def get_bot_token(param_prefix: str) -> str:
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if token:
        return token
    import boto3

    response = boto3.client("ssm").get_parameter(
        Name=f"{param_prefix}/secrets/discord_bot_token", WithDecryption=True
    )
    return response["Parameter"]["Value"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--application-id", required=True)
    parser.add_argument("--guild-id", required=True)
    parser.add_argument("--param-prefix", default="/palworld")
    args = parser.parse_args()

    url = (
        f"https://discord.com/api/v10/applications/{args.application_id}"
        f"/guilds/{args.guild_id}/commands"
    )
    request = urllib.request.Request(
        url,
        method="PUT",
        data=json.dumps(COMMANDS, ensure_ascii=False).encode(),
        headers={
            "Authorization": f"Bot {get_bot_token(args.param_prefix)}",
            "Content-Type": "application/json",
            "User-Agent": "DiscordBot (https://github.com/okdryk/discord-bot, 1.0)",
        },
    )
    with urllib.request.urlopen(request) as response:
        registered = json.loads(response.read())
    print(f"登録完了: {[c['name'] for c in registered]}")


if __name__ == "__main__":
    main()
