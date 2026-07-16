from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_events as events,
    aws_events_targets as targets,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_s3 as s3,
    aws_ssm as ssm,
)
from constructs import Construct


class PalworldStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        instance_id = self.node.try_get_context("instance_id")
        param_prefix = self.node.try_get_context("param_prefix") or "/palworld"
        empty_minutes = str(self.node.try_get_context("empty_minutes_to_stop") or 10)
        memory_percent = str(self.node.try_get_context("memory_alert_percent") or 80)
        save_dir = self.node.try_get_context("save_dir") or ""
        service_name = self.node.try_get_context("service_name") or "palworld"
        steamcmd_dir = (
            self.node.try_get_context("steamcmd_dir") or "/home/ubuntu/.steam/steam/steamcmd"
        )
        server_install_dir = (
            self.node.try_get_context("server_install_dir")
            or "/home/ubuntu/Steam/steamapps/common/PalServer"
        )
        server_user = self.node.try_get_context("server_user") or "ubuntu"
        if not instance_id or instance_id.startswith("REPLACE_"):
            raise ValueError(
                "cdk.json の context.instance_id にパルワールド用EC2のインスタンスIDを設定してください"
            )

        instance_arn = self.format_arn(
            service="ec2", resource="instance", resource_name=instance_id
        )
        param_arn = self.format_arn(
            service="ssm", resource="parameter", resource_name=param_prefix.lstrip("/") + "/*"
        )
        run_shell_script_doc_arn = self.format_arn(
            service="ssm", account="", resource="document", resource_name="AWS-RunShellScript"
        )

        backup_bucket = s3.Bucket(
            self,
            "BackupBucket",
            removal_policy=RemovalPolicy.RETAIN,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            lifecycle_rules=[s3.LifecycleRule(expiration=Duration.days(30))],
        )

        # 3関数で lambdas/ を単一アセットとして共有し、handlerだけ変える
        code = lambda_.Code.from_asset(
            "lambdas",
            bundling={
                "image": lambda_.Runtime.PYTHON_3_12.bundling_image,
                "command": [
                    "bash",
                    "-c",
                    "pip install -r requirements.txt -t /asset-output && cp -au . /asset-output",
                ],
            },
        )

        common_env = {
            "INSTANCE_ID": instance_id,
            "PARAM_PREFIX": param_prefix,
            "BACKUP_BUCKET": backup_bucket.bucket_name,
            "EMPTY_MINUTES_TO_STOP": empty_minutes,
            "MEMORY_ALERT_PERCENT": memory_percent,
            "SAVE_DIR": save_dir,
            "SERVICE_NAME": service_name,
            "STEAMCMD_DIR": steamcmd_dir,
            "SERVER_INSTALL_DIR": server_install_dir,
            "SERVER_USER": server_user,
        }

        # デプロイ時にCloudFormationが解決してLambda環境変数に埋め込む。
        # この参照方式はSecureStringに非対応のため、パラメータはString型で
        # 登録すること(Public Keyは公開情報なのでStringで問題ない)。
        discord_public_key = ssm.StringParameter.from_string_parameter_name(
            self, "DiscordPublicKeyParam", f"{param_prefix}/secrets/discord_public_key"
        ).string_value

        def make_function(id_: str, handler: str, timeout: Duration, memory: int) -> lambda_.Function:
            return lambda_.Function(
                self,
                id_,
                runtime=lambda_.Runtime.PYTHON_3_12,
                code=code,
                handler=handler,
                timeout=timeout,
                memory_size=memory,
                environment=dict(common_env),
                log_group=logs.LogGroup(
                    self,
                    f"{id_}Logs",
                    retention=logs.RetentionDays.ONE_MONTH,
                    removal_policy=RemovalPolicy.DESTROY,
                ),
            )

        worker = make_function(
            "WorkerFunction", "worker.handler.handler", Duration.minutes(15), 256
        )
        # コマンドは失敗を自身でDiscordへ報告するため、非同期Invokeの自動リトライは
        # 二重実行(update中のsteamcmd並走・通知重複)の害しかない
        worker.configure_async_invoke(retry_attempts=0)
        interactions = make_function(
            "InteractionsFunction", "interactions.handler.handler", Duration.seconds(3), 256
        )
        monitor = make_function(
            "MonitorFunction", "monitor.handler.handler", Duration.minutes(3), 256
        )

        interactions.add_environment("WORKER_FUNCTION_NAME", worker.function_name)
        interactions.add_environment("DISCORD_PUBLIC_KEY", discord_public_key)
        monitor.add_environment("WORKER_FUNCTION_NAME", worker.function_name)

        # --- IAM ---
        read_params = iam.PolicyStatement(
            actions=["ssm:GetParameter"], resources=[param_arn]
        )
        write_params = iam.PolicyStatement(
            actions=["ssm:PutParameter"], resources=[param_arn]
        )
        describe_instances = iam.PolicyStatement(
            actions=["ec2:DescribeInstances"], resources=["*"]  # Describe系はリソース限定不可
        )
        send_command = iam.PolicyStatement(
            actions=["ssm:SendCommand"],
            resources=[run_shell_script_doc_arn, instance_arn],
        )
        get_command_invocation = iam.PolicyStatement(
            actions=["ssm:GetCommandInvocation"], resources=["*"]
        )
        get_metrics = iam.PolicyStatement(
            actions=["cloudwatch:GetMetricStatistics"], resources=["*"]
        )

        worker.grant_invoke(interactions)

        worker.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "ec2:StartInstances",
                    "ec2:StopInstances",
                    "ec2:RebootInstances",
                ],
                resources=[instance_arn],
            )
        )
        for statement in (
            describe_instances,
            send_command,
            get_command_invocation,
            read_params,
            write_params,
            get_metrics,
        ):
            worker.add_to_role_policy(statement)
            monitor.add_to_role_policy(statement)
        worker.grant_invoke(monitor)

        # --- 入口とスケジュール ---
        function_url = interactions.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.NONE
        )

        events.Rule(
            self,
            "MonitorSchedule",
            schedule=events.Schedule.rate(Duration.minutes(1)),
            targets=[targets.LambdaFunction(monitor)],
        )

        CfnOutput(
            self,
            "InteractionsEndpointUrl",
            value=function_url.url,
            description="Discord Developer Portal の Interactions Endpoint URL に設定するURL",
        )
        CfnOutput(self, "BackupBucketName", value=backup_bucket.bucket_name)
