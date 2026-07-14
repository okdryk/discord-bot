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
