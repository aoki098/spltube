import os
import re
import time
import requests
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
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


def fetch_feed(playlist_id, retries=3):
    # RSSフィードは時々404や500を返すので、数回リトライする
    # 取得できなければNone(=「取得失敗」)を返す。「新着なし」とは区別する
    for attempt in range(retries):
        try:
            resp = requests.get(
                "https://www.youtube.com/feeds/videos.xml",
                params={"playlist_id": playlist_id},
                timeout=10,
            )
            if resp.status_code == 200:
                return ET.fromstring(resp.content)
            print(f"feed fetch {playlist_id}: status {resp.status_code} (try {attempt + 1})")
        except Exception as e:
            print(f"feed fetch {playlist_id}: {e} (try {attempt + 1})")
        time.sleep(1)
    return None


def has_recent_upload(playlist_id):
    # 再生リストのRSSフィードを見て最新動画の投稿日時・タイトル・サムネを調べる
    # 再生リストに最新の動画があるかどうか
    root = fetch_feed(playlist_id)
    if root is None:
        return None  # 取得失敗(前回の値を維持するため、呼び出し側で判定する)

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


def compute_view_velocity(youtube, upload, previous_video_id, previous_view_count):
    # 動画/ショート共通: 再生数と、前回チェックからの伸びを計算する
    # uploadがNone(取得失敗)の場合も計算できないので0を返す
    if upload is None or not upload["is_recent"]:
        return 0, 0

    view_count = get_youtube_view_count(youtube, upload["video_id"])
    if previous_video_id == upload["video_id"]:
        view_velocity = view_count - previous_view_count
    else:
        view_velocity = 0
    return view_count, view_velocity

def fetch_live_data(channel, twitch_client_id, twitch_token):
    # 配信中かどうかだけを取得する(DBには触らない)
    # この関数が複数チャンネル分、並列で実行される
    if channel.platform == "twitch":
        return {
            "channel": channel,
            "live": get_twitch_stream(channel.channel_id, twitch_client_id, twitch_token),
            "live_video_id": None,
        }

    is_live, live_video_id = check_youtube_live(channel.channel_id)
    return {
        "channel": channel,
        "live": None,  # YouTube APIを使う部分は後でまとめて処理する
        "live_video_id": live_video_id if is_live else None,
    }


def fetch_uploads(channels):
    # 新着動画・ショートをRSSで取得する
    # YouTubeのRSSはレート制限が厳しいので、並列にせず間隔を空けて直列に叩く
    uploads = {}
    for channel in channels:
        if channel.platform == "twitch":
            continue

        video_playlist_id = channel.channel_id.replace("UC", "UULF", 1)
        short_playlist_id = channel.channel_id.replace("UC", "UUSH", 1)

        video = has_recent_upload(video_playlist_id)
        time.sleep(1.5)
        short = has_recent_upload(short_playlist_id)
        time.sleep(1.5)

        uploads[channel.id] = (video, short)
    return uploads


#配信中か新着道があるかをDBに記録する
class Command(BaseCommand):
    def add_arguments(self, parser):
        # live: 配信中だけをチェック(10分おき用)
        # full: 配信中+新着動画もチェック(1時間おき用)
        parser.add_argument("--mode", choices=["live", "full"], default="live")

    def handle(self, *args, **options):
        mode = options["mode"]

        load_dotenv()
        api_key = os.environ["YOUTUBE_API_KEY"]
        youtube = build("youtube", "v3", developerKey=api_key)

        twitch_client_id = os.environ["TWITCH_CLIENT_ID"]
        twitch_client_secret = os.environ["TWITCH_CLIENT_SECRET"]
        twitch_token = get_twitch_access_token(twitch_client_id, twitch_client_secret)

        channels = list(RegisteredChannel.objects.all())

        # 配信中チェックは並列で実行する(通信待ちの時間を重ねられるので速くなる)
        with ThreadPoolExecutor(max_workers=5) as executor:
            results = list(executor.map(
                lambda c: fetch_live_data(c, twitch_client_id, twitch_token),
                channels,
            ))

        # 新着動画チェックはfullモードの時だけ行う
        uploads = fetch_uploads(channels) if mode == "full" else {}

        for result in results:
            channel = result["channel"]
            video, short = uploads.get(channel.id, (None, None))
            previous_status = ChannelStatus.objects.filter(which_channel=channel).first()

            if channel.platform == "twitch":
                live = result["live"]
                view_count = 0
                view_velocity = 0
                short_view_count = 0
                short_view_velocity = 0
            else:
                prev_video_id = previous_status.latest_video_id if previous_status else ""
                prev_video_views = previous_status.latest_video_view_count if previous_status else 0
                view_count, view_velocity = compute_view_velocity(
                    youtube, video, prev_video_id, prev_video_views
                )

                prev_short_id = previous_status.latest_short_id if previous_status else ""
                prev_short_views = previous_status.latest_short_view_count if previous_status else 0
                short_view_count, short_view_velocity = compute_view_velocity(
                    youtube, short, prev_short_id, prev_short_views
                )

                if result["live_video_id"]:
                    live = get_youtube_live_details(youtube, result["live_video_id"])
                else:
                    live = EMPTY_LIVE

            # 配信中の情報は毎回更新する
            defaults = {
                "is_live": live["is_live"],
                "live_title": live["title"],
                "live_thumbnail": live["thumbnail"],
                "viewer_count": live["viewer_count"],
            }

            # 動画・ショートは「取得できた時だけ」更新する
            # (取得失敗(None)の時に上書きすると、前回の正しい情報が消えてしまうため)
            if video is not None:
                defaults.update({
                    "new_video_exist": video["is_recent"],
                    "latest_video_id": video["video_id"],
                    "latest_video_title": video["title"],
                    "latest_video_thumbnail": video["thumbnail"],
                    "latest_video_view_count": view_count,
                    "latest_video_view_velocity": view_velocity,
                })

            if short is not None:
                defaults.update({
                    "new_short_exist": short["is_recent"],
                    "latest_short_id": short["video_id"],
                    "latest_short_title": short["title"],
                    "latest_short_thumbnail": short["thumbnail"],
                    "latest_short_view_count": short_view_count,
                    "latest_short_view_velocity": short_view_velocity,
                })

            # DB
            ChannelStatus.objects.update_or_create(
                which_channel=channel,
                defaults=defaults,
            )

            if mode == "live" or channel.platform == "twitch":
                upload_result = "(新着チェックなし)"
            else:
                video_result = "取得失敗" if video is None else video["is_recent"]
                short_result = "取得失敗" if short is None else short["is_recent"]
                upload_result = f"video={video_result}, short={short_result}"

            self.stdout.write(
                f"{channel.channel_name} ({channel.platform}): "
                f"live={live['is_live']}, {upload_result}, viewers={live['viewer_count']}"
            )
