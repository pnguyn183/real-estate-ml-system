import pytest
from fastapi import HTTPException

from modeling.auth import AuthService, FileUserStore


def build_service(tmp_path):
    return AuthService(
        FileUserStore(tmp_path / "users.json"),
        "test-auth-secret-key-with-at-least-32-chars",
        token_expire_minutes=5,
    )


def test_first_registered_user_is_admin_and_next_user_is_standard(tmp_path):
    service = build_service(tmp_path)

    admin = service.register("Admin@Example.com", "StrongPass1", "Admin User")
    user = service.register("user@example.com", "StrongPass1", "Regular User")

    assert admin.email == "admin@example.com"
    assert admin.role == "admin"
    assert user.role == "user"
    assert service.authenticate("admin@example.com", "StrongPass1").id == admin.id


def test_token_round_trip_and_role_change_invalidates_old_token(tmp_path):
    service = build_service(tmp_path)
    admin = service.register("admin@example.com", "StrongPass1", "Admin User")
    token = service.create_access_token(admin)

    assert service.user_from_token(token).id == admin.id

    second_admin = service.register("second@example.com", "StrongPass1", "Second Admin")
    service.update_user_role(second_admin.id, "admin", admin)
    service.update_user_role(admin.id, "manager", admin)

    with pytest.raises(HTTPException) as exc:
        service.user_from_token(token)
    assert exc.value.status_code == 401


def test_duplicate_email_and_weak_password_are_rejected(tmp_path):
    service = build_service(tmp_path)

    with pytest.raises(HTTPException) as exc:
        service.register("weak@example.com", "password", "Weak User")
    assert exc.value.status_code == 422

    service.register("user@example.com", "StrongPass1", "User")
    with pytest.raises(HTTPException) as duplicate:
        service.register("USER@example.com", "StrongPass1", "User")
    assert duplicate.value.status_code == 409


def test_cannot_disable_or_demote_last_admin(tmp_path):
    service = build_service(tmp_path)
    admin = service.register("admin@example.com", "StrongPass1", "Admin User")

    with pytest.raises(HTTPException) as disable:
        service.set_user_active(admin.id, False, admin)
    assert disable.value.status_code == 400

    with pytest.raises(HTTPException) as demote:
        service.update_user_role(admin.id, "manager", admin)
    assert demote.value.status_code == 400
