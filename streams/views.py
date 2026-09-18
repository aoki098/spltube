from django.shortcuts import render
from .models import ChannelStatus

def channel_list(request):
    statuses = ChannelStatus.objects.select_related("which_channel").order_by("-is_live", "-viewer_count")
    return render(request, "streams/channel_list.html", {"statuses": statuses})
