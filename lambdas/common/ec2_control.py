"""パルワールド用EC2インスタンスの操作。"""
import boto3

from common import config

_ec2 = None


def ec2_client():
    global _ec2
    if _ec2 is None:
        _ec2 = boto3.client("ec2")
    return _ec2


def describe() -> dict:
    """{"state": "running"|"stopped"|..., "public_ip": str|None} を返す。"""
    response = ec2_client().describe_instances(InstanceIds=[config.INSTANCE_ID])
    instance = response["Reservations"][0]["Instances"][0]
    return {
        "state": instance["State"]["Name"],
        "public_ip": instance.get("PublicIpAddress"),
    }


def start() -> None:
    ec2_client().start_instances(InstanceIds=[config.INSTANCE_ID])


def stop() -> None:
    ec2_client().stop_instances(InstanceIds=[config.INSTANCE_ID])


def reboot() -> None:
    ec2_client().reboot_instances(InstanceIds=[config.INSTANCE_ID])


def _wait(waiter_name: str, max_minutes: int) -> None:
    waiter = ec2_client().get_waiter(waiter_name)
    waiter.wait(
        InstanceIds=[config.INSTANCE_ID],
        WaiterConfig={"Delay": 10, "MaxAttempts": max_minutes * 6},
    )


def wait_running(max_minutes: int = 5) -> None:
    _wait("instance_running", max_minutes)


def wait_stopped(max_minutes: int = 5) -> None:
    _wait("instance_stopped", max_minutes)
