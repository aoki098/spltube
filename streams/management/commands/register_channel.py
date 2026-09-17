import os
from dotenv import load_dotenv
from googleapiclient.discovery import build
from django.core.management.base import BaseCommand
from streams.models import RegisteredChannel

# ハンドル情報からそのチャンネルが登録できる
class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("handle")  # コマンド実行時に渡す引数(例: @golila-golila_mal)

    def handle(self, *args, **options):
        handle = options["handle"]

        load_dotenv()
        api_key = os.environ["YOUTUBE_API_KEY"]

        youtube = build("youtube", "v3", developerKey=api_key)
        resp = youtube.channels().list(part="snippet", forHandle=handle).execute()

        item = resp["items"][0]
        channel_id = item["id"]
        channel_name = item["snippet"]["title"]
        channel_icon = item["snippet"]["thumbnails"]["default"]["url"]

        RegisteredChannel.objects.create(
            channel_id=channel_id,
            channel_name=channel_name,
            channel_icon=channel_icon,
        )
