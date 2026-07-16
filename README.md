# Palworld Discord Bot (Lambda版)

AWS EC2上のパルワールドサーバーをDiscordのスラッシュコマンドで操作するBot。
旧版(Bot専用EC2 + discord.py常駐 + RCON + SSH)を全面的に作り直し、サーバーレス化したもの。

## アーキテクチャ

```
Discord ──(Interactions HTTPS)──> Lambda Function URL
                                     │
                          [interactions Lambda]   署名検証(Ed25519) + deferred応答を即返し
                                     │ 非同期Invoke
                          [worker Lambda] ──┬─ EC2 start/stop/reboot (boto3)
                                            ├─ SSM Run Command(EC2上で curl localhost:8212 / zip / s3 cp)
                                            └─ Discord followup webhook
EventBridge (rate 1 min)
     │
[monitor Lambda] ── プレイヤー0人×10分 → 自動停止 / メモリ80%超 → Webhook警告
```

- **ゲームサーバー操作は Palworld 公式 REST API**(RCON廃止)。ただしLambdaから直接8212に接続せず、
  **SSM Run Command で EC2 上から `curl localhost:8212`** を実行する。8212ポートを外部公開せずに済み、
  SSHも不要(22番も閉じられる)。
- 認証はすべてIAMロール(アクセスキー不使用)。シークレットは SSM Parameter Store (SecureString)。
  Discord Public Key のみ公開情報のため String 型とし、デプロイ時に interactions Lambda の
  環境変数に埋め込む(3秒応答制限内に収めるため、実行時のSSM取得を避ける)。
- セーブデータのバックアップは停止時に zip 化して S3 へ保存(30日で自動削除)。

## コマンド

| コマンド | 動作 |
|---|---|
| `/palworld start` | EC2起動 → ゲームサーバー応答確認 → IP通知 |
| `/palworld stop` | アナウンス → セーブ → シャットダウン → S3バックアップ → EC2停止 |
| `/palworld restart` | セーブしてEC2再起動(メモリ逼迫時など) |
| `/palworld status` | EC2状態・プレイヤー一覧・メモリ使用率・自動停止設定 |
| `/palworld backup` | 停止せずセーブデータをS3にバックアップ |
| `/palworld update` | セーブ → サービス停止 → steamcmdで本体更新 → サービス起動(クライアント更新後のバージョン不一致対応) |
| `/palworld autostop mode:<on\|off>` | 無人時自動停止のON/OFF |

## セットアップ

### 1. シークレットを Parameter Store に登録

```bash
# Public Keyは公開情報なのでString型。CDKがデプロイ時に解決してLambda環境変数に
# 埋め込むため、SecureStringにするとデプロイが失敗する。
# 既にSecureStringで登録済みの場合は型変更できないため、delete-parameterで削除してから再作成する。
# なお値はデプロイ時に固定されるため、Public Keyを変更した場合は再デプロイが必要
aws ssm put-parameter --name /palworld/secrets/discord_public_key --type String \
  --value '<Discord Developer PortalのPublic Key>'
aws ssm put-parameter --name /palworld/secrets/discord_bot_token --type SecureString \
  --value '<Botトークン>'
aws ssm put-parameter --name /palworld/secrets/discord_webhook_url --type SecureString \
  --value '<通知先チャンネルのWebhook URL>'
aws ssm put-parameter --name /palworld/secrets/admin_password --type SecureString \
  --value '<PalWorldSettings.iniのAdminPasswordと同じ値>'
```

状態パラメータ(`/palworld/state/*`)は未登録でもデフォルト値(自動停止ON)で動作する。

### 2. cdk.json の context を設定

- `instance_id`: パルワールド用EC2のインスタンスID
- `save_dir`: セーブディレクトリ(デフォルト: `/home/ubuntu/Steam/steamapps/common/PalServer/Pal/Saved`)
- `service_name`: systemdサービス名(デフォルト: `palworld`)
- `steamcmd_dir`: steamcmd.sh のあるディレクトリ(デフォルト: `/home/ubuntu/.steam/steam/steamcmd`)
- `server_install_dir`: サーバー本体のインストール先。systemdサービスが起動する実体と一致させること
  (デフォルト: `/home/ubuntu/Steam/steamapps/common/PalServer`)
- `server_user`: steamcmdを実行するEC2上のユーザー(デフォルト: `ubuntu`)
- `empty_minutes_to_stop` / `memory_alert_percent`: 自動停止・警告のしきい値

### 3. デプロイ

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
PATH="$PWD/.venv/bin:$PATH" npx -y aws-cdk@latest bootstrap   # 初回のみ
PATH="$PWD/.venv/bin:$PATH" npx -y aws-cdk@latest deploy
```

※ Lambdaのバンドルに Docker を使用する。
出力される `InteractionsEndpointUrl` を控える。

### 4. Discord 側の設定

1. [Discord Developer Portal](https://discord.com/developers/applications) → 対象アプリ → General Information →
   **Interactions Endpoint URL** に `InteractionsEndpointUrl` を設定して保存
   (保存時にDiscordがPING検証を行う。保存できれば疎通OK)
2. スラッシュコマンドを登録:

```bash
.venv/bin/python scripts/register_commands.py \
  --application-id <APPLICATION_ID> --guild-id <サーバーID>
```

### 5. EC2(パルワールドサーバー)側の設定

1. `PalWorldSettings.ini` に REST API を有効化:
   `RESTAPIEnabled=True`, `RESTAPIPort=8212`, `AdminPassword=<手順1と同じ値>`
2. インスタンスプロファイル(IAMロール)に以下を付与:
   - `AmazonSSMManagedInstanceCore`(SSM Run Command用)
   - `CloudWatchAgentServerPolicy`(メモリメトリクス用)
   - バックアップバケットへの `s3:PutObject`
   - `/palworld/secrets/admin_password` への `ssm:GetParameter`(+SecureString複合のため `kms:Decrypt`)
3. CloudWatch Agent の設定で `mem_used_percent` を **InstanceId ディメンションのみ**で送信:
   ```json
   {"metrics": {"append_dimensions": {"InstanceId": "${aws:InstanceId}"},
                "metrics_collected": {"mem": {"measurement": ["mem_used_percent"]}}}}
   ```
4. `zip`・AWS CLI がインストールされていることを確認
5. セキュリティグループから **8212 と 22(SSH)のインバウンドを削除**(ゲームポート 8211/UDP のみ残す)
6. 旧Bot用EC2の停止と、旧Botが使っていたIAMアクセスキーの無効化

### 6. 動作確認

Discordで `/palworld status` → `/palworld start` → ゲーム接続 → `/palworld stop` の順に確認。
自動停止は、起動したまま誰もログインせず10分放置すると発動し、Webhookで通知される。

## 開発

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/          # ユニットテスト
PATH="$PWD/.venv/bin:$PATH" npx -y aws-cdk@latest synth -c instance_id=i-xxxx  # テンプレート検証
```

## ディレクトリ構成

```
├── app.py / cdk.json / stacks/     # CDK (インフラ定義)
├── lambdas/
│   ├── interactions/               # 受付: 署名検証 + deferred応答
│   ├── worker/                     # 実処理: コマンド実行・停止シーケンス
│   ├── monitor/                    # 毎分監視: 自動停止・メモリ警告
│   └── common/                     # 共通: REST API / SSM / EC2 / Discord / 状態管理
├── scripts/register_commands.py    # スラッシュコマンド登録
└── tests/
```
