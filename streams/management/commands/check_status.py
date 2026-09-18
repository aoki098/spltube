import os
import re
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from googleapiclient.discovery import build
from django.core.management.base import BaseCommand
from streams.models import RegisteredChannel, ChannelStatus


def get_twitch_access_token(client_id, client_secret):
    resp = requests.post(
        "https://id.twitch.tv/oauth2/token",
        params={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        },
    )
    return resp.json()["access_token"]


def get_twitch_stream(username, client_id, access_token):
    resp = requests.get(
        "https://api.twitch.tv/helix/streams",
        params={"user_login": username},
        headers={
            "Client-Id": client_id,
            "Authorization": f"Bearer {access_token}",
        },
    )
    data = resp.json().get("data", [])
    if not data:
        return False, 0
    return True, data[0].get("viewer_count", 0)


def check_youtube_live(channel_id):
    # チャンネルの/liveページを直接見て配信中か判定する
    resp = requests.get(f"https://www.youtube.com/channel/{channel_id}/live")
    is_live = '"isLiveNow":true' in resp.text
    video_id = None
    if is_live:
        match = re.search(r'"videoId":"([\w-]{11})"', resp.text)
        if match:
            video_id = match.group(1)
    return is_live, video_id


def get_youtube_concurrent_viewers(youtube, video_id):
    try:
        resp = youtube.videos().list(
            part="liveStreamingDetails", id=video_id
        ).execute()
        details = resp["items"][0]["liveStreamingDetails"]
        return int(details.get("concurrentViewers", 0))
    except Exception:
        return 0


ATOM_NS = "{http://www.w3.org/2005/Atom}"
YT_NS = "{http://www.youtube.com/xml/schemas/2015}"


def has_recent_upload(playlist_id):
    # 再生リストのRSSフィードを見て最新動画の投稿日時を調べる
    # 再生リストに最新の動画があるかどうか
    try:
        resp = requests.get(
            "https://www.youtube.com/feeds/videos.xml",
            params={"playlist_id": playlist_id},
        )
        root = ET.fromstring(resp.content)
    except Exception:
        return False, ""

    entry = root.find(f"{ATOM_NS}entry")
    if entry is None:
        return False, ""

    video_id = entry.find(f"{YT_NS}videoId").text

    # 24時間以内の投稿だったらTrueを返す
    published_at = entry.find(f"{ATOM_NS}published").text
    published_time = datetime.fromisoformat(published_at)
    is_recent = datetime.now(timezone.utc) - published_time < timedelta(hours=24)
    return is_recent, (video_id if is_recent else "")

#配信中か新着道があるかをDBに記録する
class Command(BaseCommand):
    def handle(self, *args, **options):
        load_dotenv()
        api_key = os.environ["YOUTUBE_API_KEY"]
        youtube = build("youtube", "v3", developerKey=api_key)

        twitch_client_id = os.environ["TWITCH_CLIENT_ID"]
        twitch_client_secret = os.environ["TWITCH_CLIENT_SECRET"]
        twitch_token = get_twitch_access_token(twitch_client_id, twitch_client_secret)

        for channel in RegisteredChannel.objects.all():
            if channel.platform == "twitch":
                is_live, viewer_count = get_twitch_stream(
                    channel.channel_id, twitch_client_id, twitch_token
                )
                new_video_exist = False
                new_short_exist = False
                latest_video_id = ""
                latest_short_id = ""
            else:
                video_playlist_id = channel.channel_id.replace("UC", "UULF", 1)
                short_playlist_id = channel.channel_id.replace("UC", "UUSH", 1)

                new_video_exist, latest_video_id = has_recent_upload(video_playlist_id)
                new_short_exist, latest_short_id = has_recent_upload(short_playlist_id)

                is_live, video_id = check_youtube_live(channel.channel_id)
                if is_live and video_id:
                    viewer_count = get_youtube_concurrent_viewers(youtube, video_id)
                else:
                    viewer_count = 0

            # DB
            ChannelStatus.objects.update_or_create(
                which_channel=channel,
                defaults={
                    "is_live": is_live,
                    "new_video_exist": new_video_exist,
                    "new_short_exist": new_short_exist,
                    "latest_video_id": latest_video_id,
                    "latest_short_id": latest_short_id,
                    "viewer_count": viewer_count,
                },
            )

            self.stdout.write(
                f"{channel.channel_name} ({channel.platform}): live={is_live}, "
                f"video={new_video_exist}, short={new_short_exist}, viewers={viewer_count}"
            )
