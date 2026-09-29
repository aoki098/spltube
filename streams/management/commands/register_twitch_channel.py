import os
import requests
from dotenv import load_dotenv
from django.core.management.base import BaseCommand
from streams.models import RegisteredChannel
from streams.twitch import get_twitch_access_token


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("username")  # コマンド実行時に渡す引数(例: ninja)

    def handle(self, *args, **options):
        username = options["username"]

        load_dotenv()
        client_id = os.environ["TWITCH_CLIENT_ID"]
        client_secret = os.environ["TWITCH_CLIENT_SECRET"]
        access_token = get_twitch_access_token(client_id, client_secret)

        resp = requests.get(
            "https://api.twitch.tv/helix/users",
            params={"login": username},
            headers={
                "Client-Id": client_id,
                "Authorization": f"Bearer {access_token}",
            },
        )
        user = resp.json()["data"][0]

        RegisteredChannel.objects.create(
            channel_id=user["login"],
            channel_name=user["display_name"],
            channel_icon=user["profile_image_url"],
            platform="twitch",
        )

        self.stdout.write(f"registered: {user['display_name']} (twitch)")
