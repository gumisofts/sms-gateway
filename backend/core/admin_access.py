"""Sidebar visibility callbacks (UNFOLD["SIDEBAR"] item "permission")."""


def _has(perm):
    def check(request):
        return request.user.is_superuser or request.user.has_perm(perm)

    return check


view_messages = _has("messaging.view_smsmessage")
view_batches = _has("messaging.view_smsbatch")
bulk_send = _has("messaging.add_smsbatch")
view_nodes = _has("nodes.view_node")
view_sims = _has("nodes.view_nodesim")
view_api_keys = _has("messaging.view_apikey")
view_users = _has("accounts.view_user")
view_groups = _has("auth.view_group")
view_node_tokens = _has("nodes.view_nodetoken")
