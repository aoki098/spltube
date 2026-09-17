import os
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from googleapiclient.discovery import build
from django.core.management.base import BaseCommand
from streams.models import RegisteredChannel, ChannelStatus


def has_recent_upload(youtube, playlist_id):
    # 再生リストに最新の動画があるかどうか
    try:
        resp = youtube.playlistItems().list(
            playlistId=playlist_id, part="snippet", maxResults=1
        ).execute()
    except Exception:
        return False
    
    # itemに再生リストの最新の動画情報を入れる
    items = resp.get("items", [])
    if not items:
        return False
    
    # 24時間以内の投稿だったらTrueを返す
    published_at = items[0]["snippet"]["publishedAt"]
    published_time = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    return datetime.now(timezone.utc) - published_time < timedelta(hours=24)

#配信中か新着道があるかをDBに記録する
class Command(BaseCommand):
    def handle(self, *args, **options):
        load_dotenv()
        api_key = os.environ["YOUTUBE_API_KEY"]
        youtube = build("youtube", "v3", developerKey=api_key)

        for channel in RegisteredChannel.objects.all():
            video_playlist_id = channel.channel_id.replace("UC", "UULF", 1)
            short_playlist_id = channel.channel_id.replace("UC", "UUSH", 1)

            new_video_exist = has_recent_upload(youtube, video_playlist_id)
            new_short_exist = has_recent_upload(youtube, short_playlist_id)

            live_resp = youtube.search().list(
                channelId=channel.channel_id,
                eventType="live",
                type="video",
                part="snippet",
            ).execute()
            is_live = len(live_resp.get("items", [])) > 0

            # DB
            ChannelStatus.objects.update_or_create(
                which_channel=channel,
                defaults={
                    "is_live": is_live,
                    "new_video_exist": new_video_exist,
                    "new_short_exist": new_short_exist,
                },
            )

            self.stdout.write(
                f"{channel.channel_name}: live={is_live}, "
                f"video={new_video_exist}, short={new_short_exist}"
            )
