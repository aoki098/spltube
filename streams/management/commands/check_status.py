import os
import re
import requests
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
    try:
        resp = requests.get(f"https://www.youtube.com/channel/{channel_id}/live", timeout=30)
    except Exception as e:
        # 取得失敗(前回の値を維持するため、Noneを返して「配信していない」と区別する)
        print(f"live page failed for {channel_id}: {str(e)[:120]}")
        return None, None

    if resp.status_code != 200:
        print(f"live page failed for {channel_id}: status={resp.status_code}")
        return None, None

    # 配信中だと/liveは視聴ページを返し、メインの動画情報にこの印が付く
    # (以前は"isLiveNow":trueを見ていたが、YouTube側のHTMLが変わって使えなくなった)
    is_live = '"isLive":true' in resp.text

    video_id = None
    if is_live:
        # ページ内に最初に出てくるvideoIdはおすすめ動画のものなので使えない
        # 「今このページが開いている動画」を指すcurrentVideoEndpointから取る
        match = re.search(r'"currentVideoEndpoint":\{.*?"videoId":"([\w-]{11})"', resp.text)
        if match:
            video_id = match.group(1)

    return is_live, video_id


def fetch_video_details(youtube, video_ids):
    # 複数の動画情報を1回のAPI呼び出しでまとめて取得する
    # videos.listはIDをカンマ区切りで最大50件まで渡せて、それでも消費は1クォータで済む
    # (1本ずつ呼ぶとチャンネル数に比例して消費が増えてしまうため)
    details = {}
    unique_ids = list(dict.fromkeys(video_ids))  # 重複を除く(順番は保たれる)

    for start in range(0, len(unique_ids), 50):
        chunk = unique_ids[start:start + 50]
        try:
            resp = youtube.videos().list(
                part="snippet,statistics,liveStreamingDetails",
                id=",".join(chunk),
                maxResults=50,
            ).execute()
        except Exception as e:
            print(f"videos.list failed: {str(e)[:120]}")
            continue

        for item in resp.get("items", []):
            details[item["id"]] = item

    return details


def get_youtube_live_details(details, video_id):
    # まとめ取得した結果から配信情報を取り出す(ここではAPIを呼ばない)
    item = details.get(video_id)
    if item is None:
        # 取得失敗(前回の値を維持するため、Noneを返してEMPTY_LIVEと区別する)
        return None

    snippet = item.get("snippet", {})
    thumbnails = snippet.get("thumbnails", {})
    return {
        "is_live": True,
        "viewer_count": int(item.get("liveStreamingDetails", {}).get("concurrentViewers", 0)),
        "title": snippet.get("title", ""),
        "thumbnail": thumbnails.get("high", thumbnails.get("default", {})).get("url", ""),
    }


EMPTY_UPLOAD = {"is_recent": False, "video_id": "", "title": "", "thumbnail": ""}


def has_recent_upload(youtube, playlist_id):
    # 公式APIで再生リストの最新動画を1件調べる(1回1クォータ)
    # 以前はRSSフィードを使っていたが、レート制限で404を頻発したため公式APIに戻した
    try:
        resp = youtube.playlistItems().list(
            part="snippet", playlistId=playlist_id, maxResults=1
        ).execute()
    except Exception as e:
        # 404は再生リスト自体が存在しない場合(ショートを一度も投稿していないチャンネルなど)
        # これは取得失敗ではなく「新着なし」が正解なのでEMPTY_UPLOADを返す
        if getattr(getattr(e, "resp", None), "status", None) == 404:
            return EMPTY_UPLOAD

        # それ以外は取得失敗(前回の値を維持するため、Noneを返して「新着なし」と区別する)
        print(f"playlistItems failed for {playlist_id}: {str(e)[:120]}")
        return None

    items = resp.get("items", [])
    if not items:
        return EMPTY_UPLOAD

    snippet = items[0]["snippet"]

    # 24時間以内の投稿だったらTrueを返す
    published_at = snippet["publishedAt"]
    published_time = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    is_recent = datetime.now(timezone.utc) - published_time < timedelta(hours=24)

    if not is_recent:
        return EMPTY_UPLOAD

    thumbnails = snippet.get("thumbnails", {})
    thumbnail = thumbnails.get("high", thumbnails.get("default", {})).get("url", "")

    return {
        "is_recent": True,
        "video_id": snippet["resourceId"]["videoId"],
        "title": snippet["title"],
        "thumbnail": thumbnail,
    }


def get_youtube_view_count(details, video_id):
    # まとめ取得した結果から再生数を取り出す(ここではAPIを呼ばない)
    item = details.get(video_id)
    if item is None:
        return 0
    return int(item.get("statistics", {}).get("viewCount", 0))


def compute_view_velocity(details, upload, previous_video_id, previous_view_count):
    # 動画/ショート共通: 再生数と、前回チェックからの伸びを計算する
    # uploadがNone(取得失敗)の場合も計算できないので0を返す
    if upload is None or not upload["is_recent"]:
        return 0, 0

    view_count = get_youtube_view_count(details, upload["video_id"])
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
            "live_check_failed": False,
        }

    is_live, live_video_id = check_youtube_live(channel.channel_id)
    return {
        "channel": channel,
        "live": None,  # YouTube APIを使う部分は後でまとめて処理する
        "live_video_id": live_video_id if is_live else None,
        "live_check_failed": is_live is None,
    }


def fetch_uploads(youtube, channels):
    # 新着動画・ショートを公式APIで取得する
    # APIクライアントはスレッドセーフではないので直列で実行する(1件0.3秒程度なので十分速い)
    uploads = {}
    for channel in channels:
        if channel.platform == "twitch":
            continue

        video_playlist_id = channel.channel_id.replace("UC", "UULF", 1)
        short_playlist_id = channel.channel_id.replace("UC", "UUSH", 1)

        video = has_recent_upload(youtube, video_playlist_id)
        short = has_recent_upload(youtube, short_playlist_id)

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
        uploads = fetch_uploads(youtube, channels) if mode == "full" else {}

        # 詳細を知りたい動画IDを先に全部集める(配信中の動画 + 新着動画 + 新着ショート)
        video_ids = []
        for result in results:
            if result["live_video_id"]:
                video_ids.append(result["live_video_id"])
        for video, short in uploads.values():
            for upload in (video, short):
                if upload is not None and upload["is_recent"]:
                    video_ids.append(upload["video_id"])

        # 50件ずつまとめて取得する(チャンネルが増えてもクォータ消費がほとんど増えない)
        details = fetch_video_details(youtube, video_ids)

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
                    details, video, prev_video_id, prev_video_views
                )

                prev_short_id = previous_status.latest_short_id if previous_status else ""
                prev_short_views = previous_status.latest_short_view_count if previous_status else 0
                short_view_count, short_view_velocity = compute_view_velocity(
                    details, short, prev_short_id, prev_short_views
                )

                if result["live_video_id"]:
                    live = get_youtube_live_details(details, result["live_video_id"])
                else:
                    live = EMPTY_LIVE

            if result["live_check_failed"]:
                # 配信ページ自体が取得できなかった場合
                # (配信していないのか取得に失敗したのか区別できないので、前回の値を残す)
                defaults = {} if previous_status else {"is_live": False}
            elif live is not None:
                # 配信中の情報は毎回更新する
                defaults = {
                    "is_live": live["is_live"],
                    "live_title": live["title"],
                    "live_thumbnail": live["thumbnail"],
                    "viewer_count": live["viewer_count"],
                }
            else:
                # 配信中だとスクレイピングでは分かったが、詳細を取得できなかった場合
                # (前回のタイトル・サムネイルを残すため、is_liveだけ更新する)
                defaults = {"is_live": True}

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

            if result["live_check_failed"]:
                live_result = "取得失敗"
                viewers = 0
            elif live is None:
                live_result = "True(詳細取得失敗)"
                viewers = 0
            else:
                live_result = live["is_live"]
                viewers = live["viewer_count"]

            self.stdout.write(
                f"{channel.channel_name} ({channel.platform}): "
                f"live={live_result}, {upload_result}, viewers={viewers}"
            )
