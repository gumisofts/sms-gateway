from channels.routing import URLRouter
from django.urls import path
from rest_framework.routers import DefaultRouter

from .consumers import *

router = DefaultRouter()
# Register Viewsets here


urlpatterns = router.urls + []

auth_router = URLRouter([path("test/", TestConsumer.as_asgi())])
