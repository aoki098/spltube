from django.db import models

class RegisteredChannel(models.Model):  # models「の中の」Modelという1つのクラスを継承
    channel_id = models.CharField(max_length=30,unique=True)
    channel_name = models.CharField(max_length=100)
    channel_icon = models.URLField()
    created_at = models.DateTimeField(auto_now_add=True)