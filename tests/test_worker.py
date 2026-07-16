import pytest

from worker import handler as worker


@pytest.fixture()
def stop_env(monkeypatch):
    """停止シーケンスの全外部依存を成功動作でモックし、呼び出しを記録する。"""
    calls = []

    monkeypatch.setattr(
        worker.ec2_control, "describe", lambda: {"state": "running", "public_ip": "1.2.3.4"}
    )
    monkeypatch.setattr(worker.ec2_control, "stop", lambda: calls.append("ec2_stop"))
    monkeypatch.setattr(worker.ec2_control, "wait_stopped", lambda **kw: None)
    monkeypatch.setattr(
        worker.palworld_api, "announce", lambda msg: calls.append("announce")
    )
    monkeypatch.setattr(worker.palworld_api, "save", lambda: calls.append("save"))
    monkeypatch.setattr(
        worker.palworld_api, "shutdown", lambda w, m: calls.append("shutdown")
    )
    monkeypatch.setattr(
        worker.ssm_run, "run_shell", lambda cmds, timeout_seconds=60: "backups/test.zip"
    )
    monkeypatch.setattr(worker.state, "set_empty_since", lambda v: calls.append("reset"))
    return calls


def test_parse_command():
    data = {
        "name": "palworld",
        "options": [
            {"name": "autostop", "options": [{"name": "mode", "value": "off"}]}
        ],
    }
    name, options = worker.parse_command(data)
    assert name == "autostop"
    assert options == {"mode": "off"}


def test_parse_command_no_options():
    data = {"name": "palworld", "options": [{"name": "status"}]}
    name, options = worker.parse_command(data)
    assert name == "status"
    assert options == {}


def test_stop_sequence_happy_path(stop_env):
    lines = worker.run_stop_sequence()
    assert "announce" in stop_env
    assert "save" in stop_env
    assert "shutdown" in stop_env
    assert "ec2_stop" in stop_env
    assert "reset" in stop_env
    assert not any("⚠️" in line for line in lines)


def test_stop_sequence_already_stopped(stop_env, monkeypatch):
    monkeypatch.setattr(
        worker.ec2_control, "describe", lambda: {"state": "stopped", "public_ip": None}
    )
    lines = worker.run_stop_sequence()
    assert "ec2_stop" not in stop_env
    assert any("既に停止" in line for line in lines)


def test_stop_sequence_save_failure_still_stops_ec2(stop_env, monkeypatch):
    def failing_save():
        raise RuntimeError("save failed")

    monkeypatch.setattr(worker.palworld_api, "save", failing_save)
    lines = worker.run_stop_sequence()
    assert any("セーブ失敗" in line for line in lines)
    assert "ec2_stop" in stop_env  # セーブ失敗でも停止は実施


def test_stop_sequence_shutdown_failure_falls_back_to_systemctl(stop_env, monkeypatch):
    def failing_shutdown(waittime, message):
        raise RuntimeError("api down")

    ssm_calls = []

    def fake_run_shell(commands, timeout_seconds=60):
        ssm_calls.append(commands)
        return ""

    monkeypatch.setattr(worker.palworld_api, "shutdown", failing_shutdown)
    monkeypatch.setattr(worker.ssm_run, "run_shell", fake_run_shell)
    lines = worker.run_stop_sequence()
    assert any(
        "systemctl stop" in cmd for commands in ssm_calls for cmd in commands
    )
    assert "ec2_stop" in stop_env


def test_stop_sequence_backup_failure_still_stops_ec2(stop_env, monkeypatch):
    def failing_run_shell(commands, timeout_seconds=60):
        # 停止確認(exit 0相当)は成功させ、バックアップ(zip)だけ失敗させる
        if any("zip" in c for c in commands):
            raise RuntimeError("no space")
        return ""

    monkeypatch.setattr(worker.ssm_run, "run_shell", failing_run_shell)
    lines = worker.run_stop_sequence()
    assert any("バックアップ失敗" in line for line in lines)
    assert "ec2_stop" in stop_env


@pytest.fixture()
def update_env(monkeypatch):
    """updateコマンドの全外部依存を成功動作でモックし、呼び出しを記録する。"""
    calls = {"scripts": [], "wait_for_api": 0}

    def fake_run_shell(commands, timeout_seconds=60, execution_timeout=None):
        calls["scripts"].append("\n".join(commands))
        return "Success! App '2394010' fully installed.\n"

    def fake_wait_for_api(max_wait_seconds):
        calls["wait_for_api"] += 1
        return True

    monkeypatch.setattr(
        worker.ec2_control, "describe", lambda: {"state": "running", "public_ip": "1.2.3.4"}
    )
    monkeypatch.setattr(worker.palworld_api, "announce", lambda msg: None)
    monkeypatch.setattr(worker.palworld_api, "save", lambda: None)
    monkeypatch.setattr(worker.ssm_run, "run_shell", fake_run_shell)
    monkeypatch.setattr(worker.state, "set_empty_since", lambda v: None)
    monkeypatch.setattr(worker, "wait_for_api", fake_wait_for_api)
    return calls


def test_update_happy_path(update_env):
    message = worker.cmd_update({})
    assert "⚠️" not in message
    assert "アップデート完了" in message
    # 停止→trapによる起動保証→steamcmd が1本のスクリプトに含まれ、この順である
    assert len(update_env["scripts"]) == 1
    script = update_env["scripts"][0]
    assert "set -u -o pipefail" in script
    assert (
        script.index("systemctl stop")
        < script.index("trap")
        < script.index("systemctl start")
        < script.index("steamcmd.sh")
    )
    assert "+force_install_dir" in script
    assert "+app_update 2394010 validate" in script
    assert f"sudo -u {worker.config.SERVER_USER} -H" in script
    assert update_env["wait_for_api"] == 1


def test_update_requires_running_ec2(update_env, monkeypatch):
    monkeypatch.setattr(
        worker.ec2_control, "describe", lambda: {"state": "stopped", "public_ip": None}
    )
    message = worker.cmd_update({})
    assert "起動していません" in message
    assert update_env["scripts"] == []  # SSMコマンドは一切実行されない


def test_update_failure_still_waits_for_api(update_env, monkeypatch):
    def failing_run_shell(commands, timeout_seconds=60, execution_timeout=None):
        raise worker.ssm_run.SsmRunError("Failed: exit 8")

    monkeypatch.setattr(worker.ssm_run, "run_shell", failing_run_shell)
    message = worker.cmd_update({})
    assert "アップデート失敗" in message
    # trapがEC2側で起動を試みているため、応答確認は行う
    assert update_env["wait_for_api"] == 1


def test_update_timeout_reports_continuation(update_env, monkeypatch):
    def timeout_run_shell(commands, timeout_seconds=60, execution_timeout=None):
        raise worker.ssm_run.SsmRunTimeout("540秒以内に完了しませんでした")

    monkeypatch.setattr(worker.ssm_run, "run_shell", timeout_run_shell)
    message = worker.cmd_update({})
    assert "継続中" in message
    assert "status" in message
    assert update_env["wait_for_api"] == 0  # 完了していないので応答確認はしない


def test_update_unrecognized_output_is_reported(update_env, monkeypatch):
    def odd_output_run_shell(commands, timeout_seconds=60, execution_timeout=None):
        return "Update state (0x61) downloading, progress: 12.3\n"

    monkeypatch.setattr(worker.ssm_run, "run_shell", odd_output_run_shell)
    message = worker.cmd_update({})
    assert "確認できませんでした" in message


def test_handler_reports_error_to_discord(monkeypatch):
    messages = []

    def failing_describe():
        raise RuntimeError("boom")

    monkeypatch.setattr(worker.ec2_control, "describe", failing_describe)
    monkeypatch.setattr(
        worker.discord_api,
        "edit_original_response",
        lambda app_id, token, content: messages.append(content),
    )

    event = {
        "kind": "command",
        "application_id": "a",
        "interaction_token": "t",
        "data": {"name": "palworld", "options": [{"name": "status"}]},
    }
    worker.handler(event, None)
    assert len(messages) == 1
    assert "エラー" in messages[0]
