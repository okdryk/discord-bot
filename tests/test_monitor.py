from datetime import datetime, timedelta, timezone

from monitor import handler as monitor

NOW = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)


def test_players_present_resets_marker():
    empty_since = NOW - timedelta(minutes=5)
    assert monitor.evaluate_auto_stop(NOW, empty_since, 2, 10) == "reset"


def test_players_present_without_marker_does_nothing():
    assert monitor.evaluate_auto_stop(NOW, None, 2, 10) == "none"


def test_first_empty_observation_marks_time():
    assert monitor.evaluate_auto_stop(NOW, None, 0, 10) == "mark"


def test_empty_below_threshold_waits():
    empty_since = NOW - timedelta(minutes=9, seconds=59)
    assert monitor.evaluate_auto_stop(NOW, empty_since, 0, 10) == "none"


def test_empty_at_threshold_stops():
    empty_since = NOW - timedelta(minutes=10)
    assert monitor.evaluate_auto_stop(NOW, empty_since, 0, 10) == "stop"


def test_empty_beyond_threshold_stops():
    # Lambdaの実行取りこぼしで観測が遅れても止まる(タイムスタンプ差分方式)
    empty_since = NOW - timedelta(hours=2)
    assert monitor.evaluate_auto_stop(NOW, empty_since, 0, 10) == "stop"
