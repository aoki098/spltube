"""Twitch APIを呼ぶための共通処理。

check_statusとregister_twitch_channelの両方から使うので、
同じ処理を2箇所に書かないようここにまとめている。
"""

import requests


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
