from celery import shared_task

from . import services


@shared_task
def mark_stale_nodes_offline() -> int:
    return services.mark_stale_offline()
