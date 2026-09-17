from django.db import models

class RegisteredChannel(models.Model):  # models「の中の」Modelという1つのクラスを継承
    channel_id = models.CharField(max_length=30,unique=True)
    channel_name = models.CharField(max_length=100)
    channel_icon = models.URLField()
    created_at = models.DateTimeField(auto_now_add=True)

class ChannelStatus(models.Model):
    which_channel = models.OneToOneField(RegisteredChannel, on_delete=models.CASCADE)
    is_live = models.BooleanField()
    new_video_exist = models.BooleanField()
    new_short_exist = models.BooleanField(default=False)
    checked_at = models.DateTimeField(auto_now=True)