import django_filters

from .models import SmsMessage, Status


class SmsMessageFilter(django_filters.FilterSet):
    status = django_filters.MultipleChoiceFilter(choices=Status.choices)
    node = django_filters.CharFilter(field_name="node__node_id")
    created_after = django_filters.IsoDateTimeFilter(
        field_name="created_at", lookup_expr="gte"
    )
    created_before = django_filters.IsoDateTimeFilter(
        field_name="created_at", lookup_expr="lte"
    )

    class Meta:
        model = SmsMessage
        fields = ["status", "node", "to", "client_reference"]
