"""コマンド実処理のワーカーLambda。

interactions Lambda から非同期Invokeされ、結果を interaction の
followup webhook (PATCH) で返す。monitor Lambda からの自動停止
({"kind": "auto_stop"}) はチャンネルWebhookで通知する。
"""
import logging
import time

from common import (
    config,
    discord_api,
    ec2_control,
    metrics,
    palworld_api,
    ssm_run,
    state,
)

logger = logging.getLogger(__name__)
logging.getLogger().setLevel(logging.INFO)


def handler(event, context):
    kind = event.get("kind")

    if kind == "auto_stop":
        # 通知を先に送信(停止シーケンスに数分かかるため)
        discord_api.post_channel_webhook(
            f"⏰ {config.EMPTY_MINUTES_TO_STOP}分間プレイヤーがいないため自動停止します"
        )
        lines = run_stop_sequence()
        if lines:
            try:
                discord_api.post_channel_webhook("\n".join(lines))
            except Exception:
                logger.exception("failed to post stop sequence results to Discord")
        return

    if kind != "command":
        logger.error("unknown event kind: %s", kind)
        return

    name, options = parse_command(event["data"])
    command = COMMANDS.get(name)
    try:
        if command is None:
            message = f"⚠️ 未知のコマンドです: {name}"
        else:
            message = command(options)
    except Exception as error:  # 失敗してもDiscordに必ず結果を返す
        logger.exception("command %s failed", name)
        message = f"⚠️ エラーが発生しました: {error}"
    discord_api.edit_original_response(
        event["application_id"], event["interaction_token"], message
    )


def parse_command(data: dict) -> tuple[str, dict]:
    """/palworld <subcommand> [options] からサブコマンド名とオプションを取り出す。"""
    subcommand = data["options"][0]
    options = {o["name"]: o["value"] for o in subcommand.get("options", [])}
    return subcommand["name"], options


# --- 各コマンド ---

def cmd_start(options: dict) -> str:
    info = ec2_control.describe()
    if info["state"] == "running":
        return f"ℹ️ サーバーは既に起動しています (IP: {info['public_ip']})"
    if info["state"] not in ("stopped",):
        return f"⚠️ EC2が {info['state']} 状態のため起動できません。少し待って再実行してください"

    ec2_control.start()
    ec2_control.wait_running()
    api_ok = wait_for_api(max_wait_seconds=300)
    ip = ec2_control.describe()["public_ip"]
    if api_ok:
        return f"✅ サーバー起動完了! IP: `{ip}`"
    return f"⚠️ EC2は起動しました (IP: `{ip}`) が、ゲームサーバーの応答をまだ確認できていません"


def cmd_stop(options: dict) -> str:
    return "\n".join(run_stop_sequence())


def cmd_restart(options: dict) -> str:
    info = ec2_control.describe()
    if info["state"] != "running":
        return f"⚠️ サーバーが起動していません (EC2: {info['state']})"

    lines = []
    try:
        palworld_api.announce("サーバーを再起動します")
    except Exception:
        lines.append("⚠️ アナウンス失敗(続行)")
    try:
        palworld_api.save()
        lines.append("✅ セーブ完了")
    except Exception:
        lines.append("⚠️ セーブ失敗(続行)")

    ec2_control.reboot()
    time.sleep(30)  # rebootの反映を待ってからAPI復帰確認
    if wait_for_api(max_wait_seconds=360):
        lines.append("✅ 再起動完了!")
    else:
        lines.append("⚠️ 再起動後のゲームサーバー応答をまだ確認できていません")
    return "\n".join(lines)


def cmd_status(options: dict) -> str:
    info = ec2_control.describe()
    lines = [f"EC2: `{info['state']}`"]
    if info["state"] == "running":
        lines[0] += f" (IP: `{info['public_ip']}`)"
        try:
            players = palworld_api.get_players()
            if players:
                names = ", ".join(p.get("name", "?") for p in players)
                lines.append(f"プレイヤー: {len(players)}人 ({names})")
            else:
                lines.append("プレイヤー: 0人")
        except Exception:
            lines.append("プレイヤー: REST API応答なし(起動処理中?)")
        memory = metrics.get_memory_used_percent()
        if memory is not None:
            lines.append(f"メモリ使用率: {memory:.1f}%")
    lines.append(
        "自動停止: " + ("ON" if state.is_auto_stop_enabled() else "OFF")
    )
    return "\n".join(lines)


def cmd_autostop(options: dict) -> str:
    enabled = options.get("mode") == "on"
    state.set_auto_stop_enabled(enabled)
    state.set_empty_since(None)
    return f"✅ 自動停止を {'ON' if enabled else 'OFF'} にしました"


def cmd_backup(options: dict) -> str:
    info = ec2_control.describe()
    if info["state"] != "running":
        return f"⚠️ サーバーが起動していません (EC2: {info['state']})"
    try:
        palworld_api.save()
    except Exception:
        return "⚠️ セーブに失敗したためバックアップを中止しました"
    key = run_backup()
    return f"✅ バックアップ完了: `s3://{config.BACKUP_BUCKET}/{key}`"


COMMANDS = {
    "start": cmd_start,
    "stop": cmd_stop,
    "restart": cmd_restart,
    "status": cmd_status,
    "autostop": cmd_autostop,
    "backup": cmd_backup,
}


# --- 停止シーケンス ---

def run_stop_sequence() -> list[str]:
    """announce→save→shutdown→停止確認→バックアップ→EC2停止。

    各ステップの成否を積み、原則後続を続行する(結果はまとめて報告)。
    """
    lines = []
    info = ec2_control.describe()
    if info["state"] in ("stopped", "stopping"):
        return ["ℹ️ サーバーは既に停止しています/停止処理中です"]

    try:
        palworld_api.announce("サーバーを1分後に停止します")
    except Exception:
        lines.append("⚠️ アナウンス失敗(続行)")

    try:
        palworld_api.save()
        lines.append("✅ セーブ完了")
    except Exception:
        lines.append("⚠️ セーブ失敗(続行)")

    try:
        palworld_api.shutdown(60, "サーバーを1分後に停止します")
        lines.append("✅ シャットダウン要求送信")
    except Exception:
        try:
            ssm_run.run_shell(
                [f"sudo systemctl stop {config.SERVICE_NAME}"], timeout_seconds=120
            )
            lines.append("⚠️ REST APIでの停止に失敗、systemctlで停止しました")
        except Exception:
            lines.append("⚠️ ゲームサーバーの停止に失敗(続行)")

    if not wait_for_service_inactive():
        lines.append("⚠️ ゲームサーバーの停止を確認できませんでした(続行)")

    try:
        key = run_backup()
        lines.append(f"✅ バックアップ: `s3://{config.BACKUP_BUCKET}/{key}`")
    except Exception as error:
        logger.exception("backup failed")
        lines.append(f"⚠️ バックアップ失敗(続行): {error}")

    try:
        ec2_control.stop()
        ec2_control.wait_stopped()
        lines.append("✅ EC2停止完了")
    except Exception as error:
        logger.exception("ec2 stop failed")
        lines.append(f"⚠️ EC2の停止に失敗: {error}")

    state.set_empty_since(None)
    return lines


def wait_for_service_inactive(max_seconds: int = 240) -> bool:
    """shutdown(waittime=60)後、ゲームサーバーのプロセス終了を待つ。

    ポーリング自体をEC2上の1回のSSMコマンドで行い、SSM往復を減らす。
    """
    attempts = max_seconds // 10
    script = (
        f'for i in $(seq 1 {attempts}); do '
        f'st=$(systemctl is-active {config.SERVICE_NAME} 2>/dev/null || true); '
        'if [ "$st" != "active" ] && [ "$st" != "activating" ]; then exit 0; fi; '
        "sleep 10; done; exit 1"
    )
    try:
        ssm_run.run_shell([script], timeout_seconds=max_seconds + 30)
        return True
    except Exception:
        return False


def run_backup() -> str:
    """セーブディレクトリをzipしてS3へアップロードし、S3キーを返す。"""
    script = [
        "set -eu -o pipefail",
        "ts=$(date +%Y%m%d_%H%M%S)",
        f"cd \"$(dirname '{config.SAVE_DIR}')\"",
        f"zip -qr \"/tmp/palworld_save_${{ts}}.zip\" \"$(basename '{config.SAVE_DIR}')\"",
        (
            'aws s3 cp "/tmp/palworld_save_${ts}.zip"'
            f' "s3://{config.BACKUP_BUCKET}/backups/palworld_save_${{ts}}.zip"'
            f" --region {config.AWS_REGION} --only-show-errors"
        ),
        'rm -f "/tmp/palworld_save_${ts}.zip"',
        'echo "backups/palworld_save_${ts}.zip"',
    ]
    output = ssm_run.run_shell(script, timeout_seconds=300)
    return output.strip().splitlines()[-1]


def wait_for_api(max_wait_seconds: int) -> bool:
    """REST APIが応答するまで待つ(EC2起動直後はSSM Agent自体も未接続のことがある)。"""
    deadline = time.monotonic() + max_wait_seconds
    while time.monotonic() < deadline:
        try:
            palworld_api.get_info()
            return True
        except Exception:
            time.sleep(15)
    return False
