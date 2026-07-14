"""CloudWatch (CWAgent) からメモリ使用率を取得する。

EC2側のCloudWatch Agentは append_dimensions を InstanceId のみで
設定しておくこと(README参照)。
"""
from datetime import datetime, timedelta, timezone

import boto3

from common import config

_cloudwatch = None


def cloudwatch_client():
    global _cloudwatch
    if _cloudwatch is None:
        _cloudwatch = boto3.client("cloudwatch")
    return _cloudwatch


def get_memory_used_percent() -> float | None:
    """直近5分の mem_used_percent の最大値。データがなければ None。"""
    end = datetime.now(timezone.utc)
    response = cloudwatch_client().get_metric_statistics(
        Namespace="CWAgent",
        MetricName="mem_used_percent",
        Dimensions=[{"Name": "InstanceId", "Value": config.INSTANCE_ID}],
        StartTime=end - timedelta(minutes=5),
        EndTime=end,
        Period=60,
        Statistics=["Average"],
    )
    datapoints = response["Datapoints"]
    if not datapoints:
        return None
    return max(point["Average"] for point in datapoints)
