import discord 
from discord import app_commands 
from discord.ext import tasks
import asyncio
from dotenv import load_dotenv
import os

from mymodule import *

load_dotenv()

intents = discord.Intents.default() 
client = discord.Client(intents=intents) 
tree = app_commands.CommandTree(client)
need_reboot_flag = True
auto_stop_ec2_flag = True
launch_time_count = 0

@tasks.loop(seconds=60)
async def monitoring():
    """ゲームサーバーの状態を確認し、誰もログインしていなければサーバーを停止する"""
    #起動していなければなにもしない
    ec2_state = check_ec2_instance_state()
    if ec2_state!='running':
        set_no_login_users_count(0)
        return
        
    login_users = get_login_users()
    if len(login_users)==0:
        count = get_no_login_users_count()
        global auto_stop_ec2_flag
        if count>=10 and auto_stop_ec2_flag :
            set_no_login_users_count(0)
            channel = client.get_channel(int(os.environ['DISCORD_CHANNEL_ID']))
            await channel.send('10分以上ログインユーザーがいないため、サーバーを停止するよ')
            await stop_pal_service(client)
            await stop_ec2_instance()
            await channel.send('停止したよ')
        else:
            set_no_login_users_count(count+1)
    else:
        set_no_login_users_count(0)

    #起動してから10分立っていない場合処理を行わない
    global launch_time_count 
    launch_time_count = launch_time_count + 1
    if launch_time_count < 10:
        return

    global need_reboot_flag
    if get_ec2_memory_used_percent() > 80 and  need_reboot_flag:
        channel = client.get_channel(int(os.environ['DISCORD_CHANNEL_ID']))
        await channel.send('メモリ使用量が80%を超えたよ。再起動するか、しないか、どっちなんだい。')
        need_reboot_flag = False

    record_player_logins()


@client.event
async def on_ready():
    print('ログインしました') 
    # アクティビティを設定 
    new_activity = "人生" 
    await client.change_presence(activity=discord.Game(new_activity)) 
    # スラッシュコマンドを同期 
    await tree.sync()
    await monitoring.start()

@tree.command(name="start_ec2_instance", description="EC2インスタンスを起動します")
async def startEC2Instance(interaction: discord.Interaction):
    await interaction.response.send_message("起動するよ。ちょっと待ってね。")
    if check_ec2_instance_state() == "running":
        await interaction.followup.send("すでに起動済みだよ。")
    else:
        await start_ec2_instance()
        while check_ec2_instance_state() != "running":
            await interaction.followup.send("起動中...")
            await asyncio.sleep(5)
        await interaction.followup.send("起動したよ。")
        global need_reboot_flag
        need_reboot_flag = True
        global launch_time_count
        launch_time_count = 0

@tree.command(name="reboot_ec2_instance", description="EC2インスタンスを再起動します")
async def startEC2Instance(interaction: discord.Interaction):
    await interaction.response.send_message("再起動するよ。ちょっと待ってね。")
    if check_ec2_instance_state() == "stopped":
        await interaction.followup.send("停止中だよ。再起動できません。")
    else:
        await stop_pal_service(client)
        await reboot_ec2_instance()
        while check_ec2_instance_state() != "running":
            await interaction.followup.send("再起動中...")
            await asyncio.sleep(5)
        await interaction.followup.send("起動したよ。")
        global need_reboot_flag
        need_reboot_flag = True
        global launch_time_count
        launch_time_count = 0

@tree.command(name="stop_ec2_instance", description="EC2インスタンスを停止します")
async def stopEC2Instance(interaction: discord.Interaction):
    await interaction.response.send_message("停止するよ。ちょっと待ってね。")
    if check_ec2_instance_state() == "stopped":
        await interaction.followup.send("すでに停止済みだよ。")
    else:
        await stop_pal_service(client)
        await stop_ec2_instance()
        await interaction.followup.send("停止したよ")

@tree.command(name="monitor_ec2_instance", description="EC2インスタンスの状態を確認します")
async def monitorEC2Instance(interaction: discord.Interaction):
    await interaction.response.send_message(check_ec2_instance_state())

@tree.command(name="change_auto_stop_ec2_instance", description="自動でサーバーを止める機能を切り替えます")
async def changeAutoStopEC2Instance(interaction: discord.Interaction):
    global auto_stop_ec2_flag
    if auto_stop_ec2_flag == True:
        auto_stop_ec2_flag = False
        await interaction.response.send_message("自動シャットダウンをオフにしたよ")
    else:
        auto_stop_ec2_flag = True
        await interaction.response.send_message("自動シャットダウンをオンにしたよ")
        

# ボットのログイン
client.run(os.environ['TOKEN'])