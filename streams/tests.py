from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from django.test import TestCase
from .models import RegisteredChannel, ChannelStatus
from .management.commands.check_status import has_recent_upload


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
    # has_recent_uploadは本物のYouTubeにアクセスするので、
    # requests.getを偽物(モック)に差し替えてテストする

    def _fake_feed(self, published_at):
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/" xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <yt:videoId>abcdefghijk</yt:videoId>
    <title>テスト動画タイトル</title>
    <published>{published_at}</published>
    <media:group>
      <media:thumbnail url="https://example.com/thumb.jpg"/>
    </media:group>
  </entry>
</feed>""".encode("utf-8")

    @patch("streams.management.commands.check_status.requests.get")
    def test_24時間以内の投稿はis_recentがTrue(self, mock_get):
        今 = datetime.now(timezone.utc).isoformat()
        mock_get.return_value.status_code = 200
        mock_get.return_value.content = self._fake_feed(今)

        result = has_recent_upload("UULFxxxxxxxxxxxxxxxxxxxxxx")

        self.assertTrue(result["is_recent"])
        self.assertEqual(result["video_id"], "abcdefghijk")
        self.assertEqual(result["title"], "テスト動画タイトル")

    @patch("streams.management.commands.check_status.requests.get")
    def test_25時間前の投稿はis_recentがFalse(self, mock_get):
        昔 = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        mock_get.return_value.status_code = 200
        mock_get.return_value.content = self._fake_feed(昔)

        result = has_recent_upload("UULFxxxxxxxxxxxxxxxxxxxxxx")

        self.assertFalse(result["is_recent"])

    @patch("streams.management.commands.check_status.requests.get")
    def test_取得に失敗したらNoneを返す(self, mock_get):
        # YouTube側が404や500を返した場合、「新着なし」ではなく「取得失敗(None)」を返す
        mock_get.return_value.status_code = 404
        mock_get.return_value.content = b"<html>not found</html>"

        result = has_recent_upload("UULFxxxxxxxxxxxxxxxxxxxxxx")

        self.assertIsNone(result)
