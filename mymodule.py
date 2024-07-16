import boto3
import discord 
import subprocess
import time
import datetime
import re
import paramiko
from dotenv import load_dotenv
import os

load_dotenv()

def ec2_client():
    return boto3.client('ec2', 
        aws_access_key_id=os.environ['API_KEY'],
        aws_secret_access_key=os.environ['SECRET_KEY'],
        region_name=os.environ['AWS_REGION'])

def connect_ssh():
    IP_ADDRESS = os.environ['AWS_EC2_INSTANCE_IP_ADRESS']
    PORT = '22'
    USER_NAME = 'ubuntu'
    KEY = "/home/ryu/.ssh/aws.pem"

    clientPm = paramiko.SSHClient()
    clientPm.set_missing_host_key_policy(paramiko.WarningPolicy()) 

    # 上記で設定したIPアドレス、ユーザー名、キーファイルを渡す
    clientPm.connect(
        IP_ADDRESS,
        username=USER_NAME,
        key_filename = KEY,
        timeout=60.0)

    return clientPm

def run_subprocess(command):
    result = subprocess.run(command, capture_output=True, text=True)
    return result.stdout

async def start_ec2_instance():
    ec2_client().start_instances(InstanceIds=[os.environ['AWS_EC2_INSTANCE_ID']])

async def reboot_ec2_instance():
    ec2_client().reboot_instances(InstanceIds=[os.environ['AWS_EC2_INSTANCE_ID']])

async def stop_ec2_instance():
    ec2_client().stop_instances(InstanceIds=[os.environ['AWS_EC2_INSTANCE_ID']])

def check_ec2_instance_state():
    response = ec2_client().describe_instances(InstanceIds=[os.environ['AWS_EC2_INSTANCE_ID']])
    state = response['Reservations'][0]['Instances'][0]['State']['Name']
    return state

async def stop_pal_service(client):
    run_subprocess(['./rcon-cli', '--host', os.environ['AWS_EC2_INSTANCE_IP_ADRESS'], '--password', 'pw','--port', '25575', 'save'])
    clientPm = connect_ssh()
    CMD = "sudo systemctl stop palworld.service"
    stdin, stdout, stderr = clientPm.exec_command(CMD)

    # 対象フォルダのバックアップを作成
    backup_command = "sudo zip Level_sav_backup.zip /home/pwserver/serverfiles/Pal/Saved/SaveGames/0/D6B46550CD9E43319262ACA125C3D7B6/Level.sav"
    stdin, stdout, stderr = clientPm.exec_command(backup_command)

    # バックアップファイルをローカルにダウンロード
    sftp = clientPm.open_sftp()
    sftp.get("/home/ubuntu/Level_sav_backup.zip", "/home/ryu/discord-bot/Level_sav_backup.zip")
    sftp.close()

    # Discord チャンネルにバックアップファイルをアップロード
    channel = client.get_channel(int(os.environ['DISCORD_CHANNEL_ID']))
    await channel.send(file=discord.File("/home/ryu/discord-bot/Level_sav_backup.zip"))

    clientPm.close()

def rcon_show_players():
    return run_subprocess(['./rcon-cli', '--host', os.environ['AWS_EC2_INSTANCE_IP_ADRESS'], '--password', 'pw','--port', '25575', 'ShowPlayers'])

def get_login_users():
    res = rcon_show_players()
    res_split = res.splitlines()[1:]
    if len(res_split) < 3:
        return []
    return res_split

def get_no_login_users_count():
    try:
        with open('logs/no_login_counter.dat', 'r') as file:
            return int(file.read())
    except:
        return 0

def set_no_login_users_count(count):
    with open('logs/no_login_counter.dat', 'w') as file:
        file.write(str(count))

def check_running_pal_world():
    return(SHOW_PLAYERS_HEADER in rcon_show_players())

def cloud_watch_client():
    return boto3.client('cloudwatch', 
            aws_access_key_id=os.environ['API_KEY'],
            aws_secret_access_key=os.environ['SECRET_KEY'],
            region_name=os.environ['AWS_REGION'])

def get_ec2_memory_used_percent():
    get_metric_statistics = cloud_watch_client().get_metric_statistics(
    Namespace='CWAgent',
    MetricName='mem_used_percent',
    Dimensions=[
    {
    'Name': 'InstanceId',
    'Value': os.environ['AWS_EC2_INSTANCE_ID']
    },
    {
        "Name": "ImageId",
        "Value": os.environ['AWS_EC2_INSTANCE_IMAGE_ID']
    },
    {
        "Name": "InstanceType",
        "Value": "m6i.xlarge"
    }
    ],
    StartTime=datetime.datetime.utcnow() - datetime.timedelta(seconds=300),
    EndTime=datetime.datetime.utcnow(),
    Period=300,
    Statistics=['Maximum'])

    return get_metric_statistics['Datapoints'][0]['Maximum']

def record_player_logins():
    player_login_counts = {}  # プレイヤーごとのログイン時間を格納する辞書
    logged_in_players = []

    with open("logs/player_login_log", 'r') as file:
        for line in file:
            # 各行をコロンで分割し、名前とカウントを取得
            name, count = line.strip().split(': ')
            # 名前とカウントを辞書に追加
    
            player_login_counts[name] = int(count)

    res = rcon_show_players()
    for item in res.splitlines()[2:-1]:
        logged_in_players.append(item.split(',')[0].replace("\x1b[0m", ""))
    

    for player in logged_in_players:
        if player in player_login_counts:
            # すでにログイン時間が記録されている場合、ログイン時間を更新
            player_login_counts[player] += 1
        else:
            # プレイヤーが新たにログインした場合、ログイン時間を1で初期化
            player_login_counts[player] = 1
    
    # ログをファイルに書き込む
    with open("logs/player_login_log", "w") as file:
        for player, login_time in player_login_counts.items():
            log_entry = f"{player}: {login_time}\n"
            file.write(log_entry)


def get_ec2_launch_time():
    return ec2_client().describe_instances(InstanceIds=[os.environ['AWS_EC2_INSTANCE_ID']])['Reservations'][0]['Instances'][0]['LaunchTime']

def get_ec2_play_time(launch_time):
    convert_time = launch_time.replace(tzinfo=None)

    return (datetime.datetime.utcnow() - convert_time).total_seconds()