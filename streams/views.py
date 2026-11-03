from django.conf import settings
from django.shortcuts import render
from .models import ChannelStatus

def channel_list(request):
    base = ChannelStatus.objects.select_related("which_channel")
    live = base.filter(is_live=True).order_by("-viewer_count")
    videos = base.filter(new_video_exist=True).order_by("-latest_video_view_velocity")
    shorts = base.filter(new_short_exist=True).order_by("-latest_short_view_velocity")
    return render(request, "streams/channel_list.html", {
        "live": live,
        "videos": videos,
        "shorts": shorts,
        "use_analytics": not settings.DEBUG,
    })
