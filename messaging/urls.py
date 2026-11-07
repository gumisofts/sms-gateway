from rest_framework.routers import DefaultRouter

from .views import ApiKeyViewSet, MessageViewSet

router = DefaultRouter()
# Mounted under /api/ next to the nodes router, which would clash on the root view.
router.include_root_view = False
router.register("messages", MessageViewSet, basename="message")
router.register("api-keys", ApiKeyViewSet, basename="api-key")

urlpatterns = router.urls
