import os

import boto3
from dotenv import load_dotenv
from django.core.management.base import BaseCommand
from django.utils import timezone

from streams.models import ChannelStatus
from .check_status import check_youtube_live

# lofi girlは24時間365日ずっと配信しているチャンネル
# ここが「配信していない」と判定されたら、判定の仕組みが壊れた可能性が高い
CANARY_CHANNEL_ID = "UCSJ4gkVC6NrvII8umztf0Ow"

# 配信チェックは10分おきに動いているので、1時間更新がなければ異常とみなす
MAX_AGE_MINUTES = 60


def check_live_detection():
    # 常時配信のチャンネルを1つだけ見て、配信中と判定できるか確かめる
    # 異常があれば内容を文字列で返し、問題なければNoneを返す
    is_live, video_id = check_youtube_live(CANARY_CHANNEL_ID)

    if is_live is None:
        return "配信ページを取得できませんでした(YouTube側の障害かレート制限の可能性)"
    if not is_live:
        return "常時配信のチャンネルが配信中と判定されませんでした(ページの構造が変わった可能性)"
    if not video_id:
        return "配信中と判定できましたが、動画IDを取り出せませんでした(構造が一部変わった可能性)"

    return None


def check_data_freshness():
    # DBが更新され続けているか確かめる(cronが止まっていないかの確認)
    latest = ChannelStatus.objects.order_by("-checked_at").first()
    if latest is None:
        return "チャンネルのデータが1件も登録されていません"

    minutes = int((timezone.now() - latest.checked_at).total_seconds() / 60)
    if minutes > MAX_AGE_MINUTES:
        return f"データが{minutes}分間更新されていません(cronが止まっている可能性)"

    return None


def send_alert(problems):
    # SNS経由でメールを送る
    # アクセスキーは使わず、EC2に付けたIAMロールで認証される
    topic_arn = os.environ["SNS_TOPIC_ARN"]
    region = os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-1")

    body = "spliveで異常を検知しました。\n\n"
    body += "\n".join(f"・{p}" for p in problems)
    body += "\n\nサーバーで以下を実行すると状況を確認できます。\n"
    body += "  cd ~/spltube && source venv/bin/activate && python manage.py check_health\n"

    boto3.client("sns", region_name=region).publish(
        TopicArn=topic_arn,
        Subject="[splive] 異常を検知しました",
        Message=body,
    )


# 壊れていても気づけない状態を避けるため、1日1回だけ健全性を確認する
class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument(
            "--test",
            action="store_true",
            help="わざと通知を送って、メールが届くかどうかを確認する",
        )

    def handle(self, *args, **options):
        load_dotenv()

        if options["test"]:
            send_alert(["これはテスト通知です。通知の仕組みが動いていることの確認です。"])
            self.stdout.write("テスト通知を送りました。メールを確認してください")
            return

        problems = [p for p in (check_live_detection(), check_data_freshness()) if p]

        if not problems:
            self.stdout.write("異常なし")
            return

        for p in problems:
            self.stdout.write(f"異常: {p}")

        send_alert(problems)
        self.stdout.write("通知を送りました")
