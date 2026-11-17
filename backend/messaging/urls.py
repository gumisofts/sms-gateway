from rest_framework.routers import DefaultRouter

from .views import ApiKeyViewSet, ContactGroupViewSet, ContactViewSet, MessageViewSet

router = DefaultRouter()
# Mounted under /api/ next to the nodes router, which would clash on the root view.
router.include_root_view = False
router.register("messages", MessageViewSet, basename="message")
router.register("contacts", ContactViewSet, basename="contact")
router.register("contact-groups", ContactGroupViewSet, basename="contact-group")
router.register("api-keys", ApiKeyViewSet, basename="api-key")

urlpatterns = router.urls
