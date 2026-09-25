from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
import httplib2
from googleapiclient.errors import HttpError
from django.test import TestCase
from .models import RegisteredChannel, ChannelStatus
from .management.commands.check_status import (
    fetch_video_details,
    get_youtube_live_details,
    get_youtube_view_count,
    has_recent_upload,
)


class RegisteredChannelTests(TestCase):
    def test_チャンネルが作成できる(self):
        channel = RegisteredChannel.objects.create(
            channel_id="UCtestxxxxxxxxxxxxxxxxx",
            channel_name="テストチャンネル",
            channel_icon="https://example.com/icon.png",
        )
        self.assertEqual(channel.channel_name, "テストチャンネル")
        self.assertEqual(channel.platform, "youtube")  # デフォルト値の確認


class ChannelStatusTests(TestCase):
    def setUp(self):
        # 各テストの前に、共通で使うチャンネルを1つ用意する
        self.channel = RegisteredChannel.objects.create(
            channel_id="UCtestxxxxxxxxxxxxxxxxx",
            channel_name="テストチャンネル",
            channel_icon="https://example.com/icon.png",
        )

    def test_配信中ステータスが作成できる(self):
        status = ChannelStatus.objects.create(
            which_channel=self.channel,
            is_live=True,
            new_video_exist=False,
        )
        self.assertTrue(status.is_live)
        self.assertEqual(status.which_channel, self.channel)

    def test_チャンネルが消えたらステータスも消える(self):
        ChannelStatus.objects.create(
            which_channel=self.channel,
            is_live=False,
            new_video_exist=False,
        )
        self.channel.delete()
        self.assertEqual(ChannelStatus.objects.count(), 0)


class ChannelListViewTests(TestCase):
    def test_一覧ページが正常に表示される(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)

    def test_配信中のチャンネルが一覧に表示される(self):
        channel = RegisteredChannel.objects.create(
            channel_id="UCtestxxxxxxxxxxxxxxxxx",
            channel_name="配信中テストチャンネル",
            channel_icon="https://example.com/icon.png",
        )
        ChannelStatus.objects.create(
            which_channel=channel,
            is_live=True,
            new_video_exist=False,
        )
        response = self.client.get("/")
        self.assertContains(response, "配信中テストチャンネル")

    def test_配信していないチャンネルは配信中セクションに出ない(self):
        # 否定側のテスト: is_live=Falseのチャンネルが、間違って表示されていないか
        channel = RegisteredChannel.objects.create(
            channel_id="UCtestxxxxxxxxxxxxxxxxx",
            channel_name="オフラインテストチャンネル",
            channel_icon="https://example.com/icon.png",
        )
        ChannelStatus.objects.create(
            which_channel=channel,
            is_live=False,
            new_video_exist=False,
        )
        response = self.client.get("/")
        self.assertNotContains(response, "オフラインテストチャンネル")

    def test_配信中は視聴者数の多い順に並ぶ(self):
        少ない = RegisteredChannel.objects.create(
            channel_id="UCsukunaixxxxxxxxxxxxx", channel_name="視聴者少ない", channel_icon="https://example.com/a.png",
        )
        多い = RegisteredChannel.objects.create(
            channel_id="UCooixxxxxxxxxxxxxxxxx", channel_name="視聴者多い", channel_icon="https://example.com/b.png",
        )
        ChannelStatus.objects.create(which_channel=少ない, is_live=True, new_video_exist=False, viewer_count=10)
        ChannelStatus.objects.create(which_channel=多い, is_live=True, new_video_exist=False, viewer_count=999)

        response = self.client.get("/")
        content = response.content.decode()
        # 「視聴者多い」の方が先に出てくるはず
        self.assertLess(content.index("視聴者多い"), content.index("視聴者少ない"))

    def test_新着動画は再生数の伸び順に並ぶ(self):
        伸び小 = RegisteredChannel.objects.create(
            channel_id="UCnobisyouxxxxxxxxxxxx", channel_name="伸び小さい動画", channel_icon="https://example.com/a.png",
        )
        伸び大 = RegisteredChannel.objects.create(
            channel_id="UCnobidaixxxxxxxxxxxxx", channel_name="伸び大きい動画", channel_icon="https://example.com/b.png",
        )
        ChannelStatus.objects.create(
            which_channel=伸び小, is_live=False, new_video_exist=True, latest_video_view_velocity=5,
        )
        ChannelStatus.objects.create(
            which_channel=伸び大, is_live=False, new_video_exist=True, latest_video_view_velocity=500,
        )

        response = self.client.get("/")
        content = response.content.decode()
        self.assertLess(content.index("伸び大きい動画"), content.index("伸び小さい動画"))


class HasRecentUploadTests(TestCase):
    # has_recent_uploadは本物のYouTube APIを呼ぶので、
    # APIクライアントを偽物(モック)に差し替えてテストする

    def _fake_youtube(self, published_at):
        youtube = MagicMock()
        youtube.playlistItems().list().execute.return_value = {
            "items": [{
                "snippet": {
                    "publishedAt": published_at,
                    "title": "テスト動画タイトル",
                    "thumbnails": {"high": {"url": "https://example.com/thumb.jpg"}},
                    "resourceId": {"videoId": "abcdefghijk"},
                }
            }]
        }
        return youtube

    def test_24時間以内の投稿はis_recentがTrue(self):
        今 = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        youtube = self._fake_youtube(今)

        result = has_recent_upload(youtube, "UULFxxxxxxxxxxxxxxxxxxxxxx")

        self.assertTrue(result["is_recent"])
        self.assertEqual(result["video_id"], "abcdefghijk")
        self.assertEqual(result["title"], "テスト動画タイトル")
        self.assertEqual(result["thumbnail"], "https://example.com/thumb.jpg")

    def test_25時間前の投稿はis_recentがFalse(self):
        昔 = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat().replace("+00:00", "Z")
        youtube = self._fake_youtube(昔)

        result = has_recent_upload(youtube, "UULFxxxxxxxxxxxxxxxxxxxxxx")

        self.assertFalse(result["is_recent"])

    def test_取得に失敗したらNoneを返す(self):
        # API呼び出しが例外を投げた場合、「新着なし」ではなく「取得失敗(None)」を返す
        youtube = MagicMock()
        youtube.playlistItems().list().execute.side_effect = Exception("API error")

        result = has_recent_upload(youtube, "UULFxxxxxxxxxxxxxxxxxxxxxx")

        self.assertIsNone(result)

    def test_404は新着なしとして扱う(self):
        # ショートを投稿していないチャンネルはUUSH再生リストが存在せず404になる
        # これは取得失敗ではないので、Noneではなく「新着なし」を返す
        youtube = MagicMock()
        エラー = HttpError(httplib2.Response({"status": 404}), b"not found")
        youtube.playlistItems().list().execute.side_effect = エラー

        result = has_recent_upload(youtube, "UUSHxxxxxxxxxxxxxxxxxxxxxx")

        self.assertIsNotNone(result)
        self.assertFalse(result["is_recent"])


class FetchVideoDetailsTests(TestCase):
    # fetch_video_detailsは動画IDをまとめてAPIに渡すことでクォータを節約する
    # 「何回API呼び出しが発生したか」がそのまま消費量なので、回数をテストする

    def _fake_youtube(self, items):
        youtube = MagicMock()
        youtube.videos.return_value.list.return_value.execute.return_value = {"items": items}
        return youtube

    def _呼び出し回数(self, youtube):
        return youtube.videos.return_value.list.call_count

    def test_50件までは1回のAPI呼び出しで済む(self):
        video_ids = [f"video{i:06d}" for i in range(50)]
        youtube = self._fake_youtube([])

        fetch_video_details(youtube, video_ids)

        self.assertEqual(self._呼び出し回数(youtube), 1)

    def test_51件だと2回に分かれる(self):
        # videos.listは1回50件までなので、超えたら分割される
        video_ids = [f"video{i:06d}" for i in range(51)]
        youtube = self._fake_youtube([])

        fetch_video_details(youtube, video_ids)

        self.assertEqual(self._呼び出し回数(youtube), 2)

    def test_同じ動画IDは重複して数えない(self):
        youtube = self._fake_youtube([])

        fetch_video_details(youtube, ["abcdefghijk"] * 60)

        self.assertEqual(self._呼び出し回数(youtube), 1)

    def test_動画IDをキーにした辞書が返る(self):
        youtube = self._fake_youtube([
            {"id": "abcdefghijk", "statistics": {"viewCount": "1234"}},
        ])

        details = fetch_video_details(youtube, ["abcdefghijk"])

        self.assertEqual(get_youtube_view_count(details, "abcdefghijk"), 1234)

    def test_API呼び出しが失敗しても落ちない(self):
        youtube = MagicMock()
        youtube.videos.return_value.list.return_value.execute.side_effect = Exception("API error")

        details = fetch_video_details(youtube, ["abcdefghijk"])

        self.assertEqual(details, {})

    def test_取得できなかった動画は再生数0になる(self):
        self.assertEqual(get_youtube_view_count({}, "abcdefghijk"), 0)

    def test_配信詳細が取得できなかったらNoneを返す(self):
        # 「配信していない」ではなく「取得失敗」なので、前回の値を残すためNoneを返す
        self.assertIsNone(get_youtube_live_details({}, "abcdefghijk"))

    def test_配信詳細から同時接続者数を取り出せる(self):
        details = {
            "abcdefghijk": {
                "snippet": {
                    "title": "テスト配信タイトル",
                    "thumbnails": {"high": {"url": "https://example.com/live.jpg"}},
                },
                "liveStreamingDetails": {"concurrentViewers": "567"},
            }
        }

        live = get_youtube_live_details(details, "abcdefghijk")

        self.assertTrue(live["is_live"])
        self.assertEqual(live["viewer_count"], 567)
        self.assertEqual(live["title"], "テスト配信タイトル")
        self.assertEqual(live["thumbnail"], "https://example.com/live.jpg")
