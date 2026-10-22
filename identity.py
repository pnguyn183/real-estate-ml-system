"""Shared role mapping for verified OIDC claims."""


def _values(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value]
    return []


def resolve_role(claims, *, admin_groups=(), manager_groups=(),
                 admin_emails=(), manager_emails=()):
    """Map provider-managed groups or verified email allowlists to app roles."""
    claim_roles = {
        str(value).strip().casefold()
        for key in ("groups", "roles", "role")
        for value in _values(claims.get(key))
        if str(value).strip()
    }
    configured_admin = {value.strip().casefold() for value in _values(admin_groups)}
    configured_manager = {value.strip().casefold() for value in _values(manager_groups)}

    email = str(claims.get("email", "")).strip().casefold()
    email_verified = claims.get("email_verified") is True
    admin_email_set = {value.strip().casefold() for value in _values(admin_emails)}
    manager_email_set = {value.strip().casefold() for value in _values(manager_emails)}

    if (claim_roles & (configured_admin | {"admin", "homenest-admin"})
            or email_verified and email in admin_email_set):
        return "Admin"
    if (claim_roles & (configured_manager | {"manager", "homenest-manager"})
            or email_verified and email in manager_email_set):
        return "Manager"
    return "User"
