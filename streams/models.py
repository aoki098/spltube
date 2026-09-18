from django.db import models

class RegisteredChannel(models.Model):  # models「の中の」Modelという1つのクラスを継承
    channel_id = models.CharField(max_length=30,unique=True)
    channel_name = models.CharField(max_length=100)
    channel_icon = models.URLField()
    created_at = models.DateTimeField(auto_now_add=True)
    platform = models.CharField(max_length=10, choices=[("youtube", "YouTube"), ("twitch", "Twitch")], default="youtube")


class ChannelStatus(models.Model):
    which_channel = models.OneToOneField(RegisteredChannel, on_delete=models.CASCADE)
    is_live = models.BooleanField()
    new_video_exist = models.BooleanField()
    new_short_exist = models.BooleanField(default=False)
    latest_video_id = models.CharField(max_length=11, blank=True, default="")
    latest_short_id = models.CharField(max_length=11, blank=True, default="")
    viewer_count = models.IntegerField(default=0)
    checked_at = models.DateTimeField(auto_now=True)