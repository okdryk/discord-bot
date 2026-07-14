# パルワールド Discord Bot 作り直し(EC2常駐 → Lambda化)

## Context

2年前に作成した「AWS EC2上のパルワールドサーバーをDiscord Botで操作する」プロジェクトの全面作り直し。現行はBot専用EC2上でdiscord.py(Gateway常駐)+ rcon-cliバイナリ + paramiko SSHで動作しており、以下の問題がある:

- Bot専用EC2の維持コスト
- IAMアクセスキー・SSH秘密鍵の平文管理
- バグ多数(関数名重複で`/reboot_ec2_instance`が動かない、RCONポート指定ミス、未定義定数、InstanceTypeハードコード等)
- requirements.txtなし、グローバル変数での状態管理、エラーハンドリング欠如

**新方針(ユーザー決定済み):**
- Bot用EC2を廃止し **Lambda + Discord Interactions Endpoint(Webhook)方式** に移行
- ゲームサーバー連携は RCON をやめ **Palworld公式REST API**
- SSH をやめ **SSM Run Command**
- セーブバックアップは Discordアップロードをやめ **S3保存**(Discordには通知のみ)
- IaC は **AWS CDK (Python)**、実装言語は **Python 継続**
- 認証はIAMロール、シークレットは SSM Parameter Store (SecureString)

既存の `main.py` / `mymodule.py` は削除し、ゼロから書き直す。パルワールド用EC2自体は既存のものを流用しCDK管理外(instance idをcontextで渡す)。

## アーキテクチャ

```
Discord ──(Interactions HTTPS)──> Lambda Function URL
                                     │
                          [interactions Lambda]   署名検証(PyNaCl) + type5(deferred)即返し
                                     │ 非同期Invoke
                          [worker Lambda] ──┬─ EC2 start/stop/reboot (boto3)
                                            ├─ SSM Run Command(EC2上で curl localhost:8212 / zip / s3 cp)
                                            └─ Discord followup webhook PATCH
EventBridge Scheduler (rate 1 min)
     │
[monitor Lambda] ── EC2停止中は即return / 稼働中: プレイヤー0人×10分→workerをInvokeで自動停止、
                    メモリ80%超→チャンネルWebhookで警告(30分クールダウン)

状態:      SSM Parameter Store /palworld/state/*  (DynamoDB不採用 — 3値だけなので過剰)
シークレット: SSM Parameter Store SecureString /palworld/secrets/*  (Secrets Managerは有料のため不採用)
バックアップ: S3バケット(ライフサイクル30日)
```

### 主要な設計判断

1. **受付/ワーカーの2 Lambda分離**: Discordの3秒応答制限のため、受付は署名検証+PING応答+deferred返却のみ(128MB/3秒)。workerは15分タイムアウトでEC2・SSM権限を持つ。コードは単一アセットを共有しhandlerだけ変える。
2. **HTTP入口はLambda Function URL**(API Gateway不使用 — 無料・設定最小。Discord署名検証があるのでAuthType=NONEで可)。
3. **REST APIへは「SSM Run CommandでEC2上から `curl localhost:8212`」**: Lambdaに固定IPがなくSGで8212を絞れない問題と、VPC内Lambda案のNAT Gateway費用(~$32/月)を両方回避。8212ポートを外部に一切開けない。AdminPasswordはEC2側で `aws ssm get-parameter` して curl に渡す(コマンド文字列に平文を埋めない)。
4. **監視の無駄起動対策はearly return方式**(スケジュールのenable/disable切替は状態不整合リスクがあるため不採用)。`describe_instances` 1回で終わるので毎分でも無料枠内。
5. **「0人10分」判定はタイムスタンプ差分**(`empty_since` に0人になった時刻を記録)。毎分カウント方式より実行取りこぼしに強い。
6. **メモリ警告は監視Lambdaに統合**(CloudWatch Alarm+SNSは構成要素が増えるだけ)。
7. **コマンド起点でない通知**(自動停止・メモリ警告)は interaction token がないため、チャンネルの通常Webhook URLで送信。

## コマンド体系(サブコマンド化)

| コマンド | 動作 |
|---|---|
| `/palworld start` | EC2起動 → running待機 → REST API応答確認(最大5分) → followup通知 |
| `/palworld stop` | 停止シーケンス(下記) |
| `/palworld restart` | announce → save → EC2 reboot → API復帰確認 → followup |
| `/palworld status` | EC2状態 + プレイヤー一覧 + メモリ使用率 |
| `/palworld autostop mode:<on\|off>` | 自動停止フラグ切替 |
| `/palworld backup` | save → zip → S3アップロードのみ(停止しない) |

### 停止シーケンス(worker `run_stop_sequence()`)

各ステップの成否を積んで最後にまとめてDiscord報告。原則続行、冪等:

1. describe → 既にstopped/stoppingなら「既に停止中」で終了
2. REST `announce`(失敗しても続行)
3. REST `save`(失敗→警告付きで続行)
4. REST `shutdown`(失敗→SSMで `systemctl stop palworld` にフォールバック)
5. SSMで `systemctl is-active` が inactive になるまでポーリング(最大3分)
6. SSMでセーブデータをzip → `aws s3 cp` でバックアップバケットへ(失敗→警告付きで続行)
7. `stop_instances` → waiter(stopped)
8. `empty_since` リセット
9. followup/webhookで結果サマリ(失敗ステップは⚠付き)

## リポジトリ構成

```
├── app.py                      # CDKエントリポイント
├── cdk.json                    # context: instance_id, guild_id 等
├── requirements.txt            # CDK用
├── requirements-dev.txt        # pytest 等
├── stacks/palworld_stack.py    # 全リソース(1スタック)
├── lambdas/
│   ├── requirements.txt        # PyNaClのみ(boto3/urllib3はランタイム同梱)
│   ├── interactions/handler.py # 署名検証 + PING + deferred + worker Invoke
│   ├── worker/handler.py       # コマンド分岐・停止シーケンス
│   ├── monitor/handler.py      # 毎分監視(early return / 自動停止 / メモリ警告)
│   └── common/
│       ├── config.py           # 環境変数・パラメータ名集約
│       ├── discord_api.py      # followup PATCH / webhook POST(urllib3使用、requests不採用)
│       ├── palworld_api.py     # SSM経由curlでREST API(players/save/shutdown/announce/info)
│       ├── ec2_control.py      # start/stop/reboot/describe + waiter
│       ├── ssm_run.py          # send_command + get_command_invocation ポーリング
│       └── state.py            # Parameter Store読み書き
├── scripts/register_commands.py # guildコマンドをPUTで全置換(冪等・手動実行)
├── tests/                      # 署名検証 / 停止シーケンス分岐 / 10分判定ロジック
└── README.md                   # デプロイ手順・シークレット登録手順
```

- 旧 `main.py` / `mymodule.py` は削除
- Lambdaは `lambdas/` を単一アセットとして3関数共有(Layer不要)。PyNaClはネイティブ拡張のためDockerバンドリング(`Code.from_asset` + bundling)でビルド
- interactions handlerの注意: Function URLの `isBase64Encoded` を考慮し raw body デコード後に署名検証。検証前にJSONパースしない

## CDKリソースとIAM要点

| リソース | 内容 |
|---|---|
| S3バケット | バックアップ用、ライフサイクル30日削除 |
| Lambda ×3 | Python 3.12、環境変数: INSTANCE_ID / WORKER_FUNCTION_NAME / PARAM_PREFIX / BACKUP_BUCKET |
| Function URL | interactions用、AuthType=NONE |
| EventBridge Scheduler | rate(1 minute) → monitor |

| 主体 | IAM権限 |
|---|---|
| interactions | `lambda:InvokeFunction`(worker限定) + public key取得 |
| worker | `ec2:Start/Stop/RebootInstances`(対象インスタンス限定)、`ec2:DescribeInstances`、`ssm:SendCommand`(AWS-RunShellScript+対象インスタンス限定)、`ssm:GetCommandInvocation`、`ssm:Get/PutParameter`(`/palworld/*`) |
| monitor | workerからStart/Stop/Reboot除外 + `cloudwatch:GetMetricStatistics` + worker Invoke |
| EC2ロール | `AmazonSSMManagedInstanceCore`、`CloudWatchAgentServerPolicy`、`s3:PutObject`(バックアップバケット限定)、`ssm:GetParameter`(AdminPassword) |

シークレット(CDK外で手動 `aws ssm put-parameter --type SecureString`):
`/palworld/secrets/discord_public_key`・`discord_bot_token`・`discord_webhook_url`・`admin_password`
状態初期値: `/palworld/state/auto_stop_enabled`=`true`、`empty_since`=`none`、`last_mem_alert`

## 実装ステップ

1. 旧コード削除、CDK雛形作成(`app.py`, `cdk.json`, `stacks/palworld_stack.py`)
2. `lambdas/common/` 一式: config → ssm_run → palworld_api → discord_api → state → ec2_control
3. interactions Lambda(署名検証+PING) → これが最初の関門(Developer PortalのEndpoint URL保存時のPING検証)
4. worker Lambda: status → start → autostop → backup → stop → restart の順
5. `scripts/register_commands.py`(guildコマンド登録)
6. monitor Lambda + Scheduler
7. tests(署名検証の正/否、停止シーケンスの分岐をStubberで、10分判定は時刻注入)
8. README(デプロイ手順、シークレット登録、EC2側設定手順)

## EC2側の準備(READMEに記載、ユーザー作業)

1. `PalWorldSettings.ini`: `RESTAPIEnabled=True`, `RESTAPIPort=8212`, `AdminPassword` 設定
2. インスタンスプロファイル付替え(SSM+S3+CloudWatch)
3. SGから **8212と22(SSH)のインバウンドを削除**(ゲームポート8211/UDPのみ残す)
4. `zip`・`awscli` インストール確認
5. 旧Bot用EC2の停止・IAMアクセスキーの無効化

## 検証方法

1. `pytest` でユニットテスト通過
2. `cdk deploy` → Function URLをDiscord Developer PortalのInteractions Endpoint URLに設定 → **PING検証が通ること**(E2Eの第一関門)
3. `python scripts/register_commands.py` → Discordで `/palworld status` 疎通確認
4. `/palworld start` → 起動通知 → ゲーム接続確認 → `/palworld stop` → S3にzipが作られEC2がstoppedになること
5. 自動停止: EC2稼働・0人のまま10分放置 → 自動停止とWebhook通知を確認
