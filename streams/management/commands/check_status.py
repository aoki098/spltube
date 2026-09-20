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


EMPTY_LIVE = {"is_live": False, "viewer_count": 0, "title": "", "thumbnail": ""}


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
        return EMPTY_LIVE

    stream = data[0]
    thumbnail = stream.get("thumbnail_url", "").replace("{width}", "440").replace("{height}", "248")
    return {
        "is_live": True,
        "viewer_count": stream.get("viewer_count", 0),
        "title": stream.get("title", ""),
        "thumbnail": thumbnail,
    }


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


def get_youtube_live_details(youtube, video_id):
    try:
        resp = youtube.videos().list(
            part="snippet,liveStreamingDetails", id=video_id
        ).execute()
        item = resp["items"][0]
        viewer_count = int(item["liveStreamingDetails"].get("concurrentViewers", 0))
        title = item["snippet"]["title"]
        thumbnail = item["snippet"]["thumbnails"]["high"]["url"]
        return {"is_live": True, "viewer_count": viewer_count, "title": title, "thumbnail": thumbnail}
    except Exception:
        return EMPTY_LIVE


ATOM_NS = "{http://www.w3.org/2005/Atom}"
YT_NS = "{http://www.youtube.com/xml/schemas/2015}"
MEDIA_NS = "{http://search.yahoo.com/mrss/}"

EMPTY_UPLOAD = {"is_recent": False, "video_id": "", "title": "", "thumbnail": ""}


def has_recent_upload(playlist_id):
    # 再生リストのRSSフィードを見て最新動画の投稿日時・タイトル・サムネを調べる
    # 再生リストに最新の動画があるかどうか
    try:
        resp = requests.get(
            "https://www.youtube.com/feeds/videos.xml",
            params={"playlist_id": playlist_id},
        )
        root = ET.fromstring(resp.content)
    except Exception:
        return EMPTY_UPLOAD

    entry = root.find(f"{ATOM_NS}entry")
    if entry is None:
        return EMPTY_UPLOAD

    # 24時間以内の投稿だったらTrueを返す
    published_at = entry.find(f"{ATOM_NS}published").text
    published_time = datetime.fromisoformat(published_at)
    is_recent = datetime.now(timezone.utc) - published_time < timedelta(hours=24)

    if not is_recent:
        return EMPTY_UPLOAD

    video_id = entry.find(f"{YT_NS}videoId").text
    title = entry.find(f"{ATOM_NS}title").text
    thumbnail_el = entry.find(f"{MEDIA_NS}group/{MEDIA_NS}thumbnail")
    thumbnail = thumbnail_el.get("url") if thumbnail_el is not None else ""

    return {"is_recent": True, "video_id": video_id, "title": title, "thumbnail": thumbnail}


def get_youtube_view_count(youtube, video_id):
    try:
        resp = youtube.videos().list(part="statistics", id=video_id).execute()
        return int(resp["items"][0]["statistics"]["viewCount"])
    except Exception:
        return 0

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
            previous_status = ChannelStatus.objects.filter(which_channel=channel).first()

            if channel.platform == "twitch":
                live = get_twitch_stream(channel.channel_id, twitch_client_id, twitch_token)
                video = EMPTY_UPLOAD
                short = EMPTY_UPLOAD
                view_count = 0
                view_velocity = 0
            else:
                video_playlist_id = channel.channel_id.replace("UC", "UULF", 1)
                short_playlist_id = channel.channel_id.replace("UC", "UUSH", 1)

                video = has_recent_upload(video_playlist_id)
                short = has_recent_upload(short_playlist_id)

                if video["is_recent"]:
                    view_count = get_youtube_view_count(youtube, video["video_id"])
                    if previous_status and previous_status.latest_video_id == video["video_id"]:
                        view_velocity = view_count - previous_status.latest_video_view_count
                    else:
                        view_velocity = 0
                else:
                    view_count = 0
                    view_velocity = 0

                is_live, video_id = check_youtube_live(channel.channel_id)
                if is_live and video_id:
                    live = get_youtube_live_details(youtube, video_id)
                else:
                    live = EMPTY_LIVE

            # DB
            ChannelStatus.objects.update_or_create(
                which_channel=channel,
                defaults={
                    "is_live": live["is_live"],
                    "new_video_exist": video["is_recent"],
                    "new_short_exist": short["is_recent"],
                    "latest_video_id": video["video_id"],
                    "latest_video_title": video["title"],
                    "latest_video_thumbnail": video["thumbnail"],
                    "latest_video_view_count": view_count,
                    "latest_video_view_velocity": view_velocity,
                    "latest_short_id": short["video_id"],
                    "latest_short_title": short["title"],
                    "latest_short_thumbnail": short["thumbnail"],
                    "live_title": live["title"],
                    "live_thumbnail": live["thumbnail"],
                    "viewer_count": live["viewer_count"],
                },
            )

            self.stdout.write(
                f"{channel.channel_name} ({channel.platform}): live={live['is_live']}, "
                f"video={video['is_recent']}, short={short['is_recent']}, viewers={live['viewer_count']}"
            )
