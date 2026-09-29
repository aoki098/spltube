from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import httplib2
from googleapiclient.errors import HttpError
from django.test import TestCase
from django.utils import timezone as django_timezone
from .models import RegisteredChannel, ChannelStatus
from .management.commands.check_health import (
    check_data_freshness,
    check_live_detection,
)
from .management.commands.check_status import (
    check_youtube_live,
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

    def _偽のyoutube(self, published_at):
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
        youtube = self._偽のyoutube(今)

        result = has_recent_upload(youtube, "UULFxxxxxxxxxxxxxxxxxxxxxx")

        self.assertTrue(result["is_recent"])
        self.assertEqual(result["video_id"], "abcdefghijk")
        self.assertEqual(result["title"], "テスト動画タイトル")
        self.assertEqual(result["thumbnail"], "https://example.com/thumb.jpg")

    def test_25時間前の投稿はis_recentがFalse(self):
        昔 = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat().replace("+00:00", "Z")
        youtube = self._偽のyoutube(昔)

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


class CheckYoutubeLiveTests(TestCase):
    # check_youtube_liveは/liveページのHTMLを見て配信中か判定する
    # 本物のページは1MB以上あるので、判定に必要な部分だけを持つ偽のHTMLを使う

    def _偽のページ(self, text, status_code=200):
        response = MagicMock()
        response.text = text
        response.status_code = status_code
        return patch("streams.management.commands.check_status.requests.get",
                     return_value=response)

    # 配信中のページの構造(おすすめ動画のvideoIdが先に出てくるのが再現ポイント)
    配信中のHTML = (
        '{"videoId":"osusumeVID1"}'
        '"viewCount":{"videoViewCountRenderer":{"isLive":true}}'
        '"currentVideoEndpoint":{"clickTrackingParams":"xxx",'
        '"watchEndpoint":{"videoId":"haishinVID1"}}'
    )

    def test_配信中ならTrueと配信の動画IDを返す(self):
        with self._偽のページ(self.配信中のHTML):
            is_live, video_id = check_youtube_live("UCtestxxxxxxxxxxxxxxxxx")

        self.assertTrue(is_live)
        # ページ先頭のおすすめ動画ではなく、今開いている配信のIDが取れること
        self.assertEqual(video_id, "haishinVID1")

    def test_配信していなければFalseを返す(self):
        with self._偽のページ('{"videoId":"osusumeVID1"}チャンネルページの中身'):
            is_live, video_id = check_youtube_live("UCtestxxxxxxxxxxxxxxxxx")

        self.assertFalse(is_live)
        self.assertIsNone(video_id)

    def test_ページが取得できなかったらNoneを返す(self):
        # 429などで弾かれた場合、「配信していない」と誤って判定してはいけない
        with self._偽のページ("", status_code=429):
            is_live, video_id = check_youtube_live("UCtestxxxxxxxxxxxxxxxxx")

        self.assertIsNone(is_live)
        self.assertIsNone(video_id)

    def test_通信自体が失敗してもNoneを返す(self):
        with patch("streams.management.commands.check_status.requests.get",
                   side_effect=Exception("connection error")):
            is_live, video_id = check_youtube_live("UCtestxxxxxxxxxxxxxxxxx")

        self.assertIsNone(is_live)
        self.assertIsNone(video_id)


class CheckHealthTests(TestCase):
    # check_healthは「壊れていることに気づけない」状態を防ぐための監視
    # 異常があれば説明の文字列を、問題なければNoneを返す作りになっている

    def _偽の判定(self, is_live, video_id):
        return patch("streams.management.commands.check_health.check_youtube_live",
                     return_value=(is_live, video_id))

    def test_常時配信が検知できていれば異常なし(self):
        with self._偽の判定(True, "haishinVID1"):
            self.assertIsNone(check_live_detection())

    def test_常時配信が検知できなければ異常とみなす(self):
        # ページの構造が変わってYouTubeの配信を拾えなくなった状態
        with self._偽の判定(False, None):
            結果 = check_live_detection()

        self.assertIsNotNone(結果)
        self.assertIn("構造", 結果)

    def test_ページを取得できなければ異常とみなす(self):
        with self._偽の判定(None, None):
            self.assertIsNotNone(check_live_detection())

    def test_動画IDが取れなければ異常とみなす(self):
        with self._偽の判定(True, None):
            self.assertIsNotNone(check_live_detection())

    def test_データが新しければ異常なし(self):
        channel = RegisteredChannel.objects.create(
            channel_id="UCtestxxxxxxxxxxxxxxxxx",
            channel_name="テストチャンネル",
            channel_icon="https://example.com/icon.png",
        )
        ChannelStatus.objects.create(which_channel=channel, is_live=False)

        self.assertIsNone(check_data_freshness())

    def test_データが古ければ異常とみなす(self):
        # cronが止まってDBが更新されなくなった状態
        channel = RegisteredChannel.objects.create(
            channel_id="UCtestxxxxxxxxxxxxxxxxx",
            channel_name="テストチャンネル",
            channel_icon="https://example.com/icon.png",
        )
        status = ChannelStatus.objects.create(which_channel=channel, is_live=False)
        # checked_atはauto_nowなので、保存後にupdateで直接書き換える
        古い時刻 = django_timezone.now() - timedelta(hours=3)
        ChannelStatus.objects.filter(pk=status.pk).update(checked_at=古い時刻)

        結果 = check_data_freshness()

        self.assertIsNotNone(結果)
        self.assertIn("更新されていません", 結果)

    def test_チャンネルが1件もなければ異常とみなす(self):
        self.assertIsNotNone(check_data_freshness())


class FetchVideoDetailsTests(TestCase):
    # fetch_video_detailsは動画IDをまとめてAPIに渡すことでクォータを節約する
    # 「何回API呼び出しが発生したか」がそのまま消費量なので、回数をテストする

    def _偽のyoutube(self, items):
        youtube = MagicMock()
        youtube.videos.return_value.list.return_value.execute.return_value = {"items": items}
        return youtube

    def _呼び出し回数(self, youtube):
        return youtube.videos.return_value.list.call_count

    def test_50件までは1回のAPI呼び出しで済む(self):
        video_ids = [f"video{i:06d}" for i in range(50)]
        youtube = self._偽のyoutube([])

        fetch_video_details(youtube, video_ids)

        self.assertEqual(self._呼び出し回数(youtube), 1)

    def test_51件だと2回に分かれる(self):
        # videos.listは1回50件までなので、超えたら分割される
        video_ids = [f"video{i:06d}" for i in range(51)]
        youtube = self._偽のyoutube([])

        fetch_video_details(youtube, video_ids)

        self.assertEqual(self._呼び出し回数(youtube), 2)

    def test_同じ動画IDは重複して数えない(self):
        youtube = self._偽のyoutube([])

        fetch_video_details(youtube, ["abcdefghijk"] * 60)

        self.assertEqual(self._呼び出し回数(youtube), 1)

    def test_動画IDをキーにした辞書が返る(self):
        youtube = self._偽のyoutube([
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

    def _配信の詳細(self, broadcast_content):
        return {
            "abcdefghijk": {
                "snippet": {
                    "title": "テスト配信タイトル",
                    "thumbnails": {"high": {"url": "https://example.com/live.jpg"}},
                    "liveBroadcastContent": broadcast_content,
                },
                "liveStreamingDetails": {"concurrentViewers": "567"},
            }
        }

    def test_配信詳細から同時接続者数を取り出せる(self):
        live = get_youtube_live_details(self._配信の詳細("live"), "abcdefghijk")

        self.assertTrue(live["is_live"])
        self.assertEqual(live["viewer_count"], 567)
        self.assertEqual(live["title"], "テスト配信タイトル")
        self.assertEqual(live["thumbnail"], "https://example.com/live.jpg")

    def test_配信待機所は配信中として扱わない(self):
        # 開始前の待機所ページも配信中と同じHTMLになるので、APIの値で弾く必要がある
        live = get_youtube_live_details(self._配信の詳細("upcoming"), "abcdefghijk")

        self.assertFalse(live["is_live"])
        self.assertEqual(live["viewer_count"], 0)
