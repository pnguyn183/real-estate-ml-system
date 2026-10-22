import os
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
import requests

import api
import api_client
import auth_store
from auth_store import AccountStore
from internal_auth import _token_payload
import storage


class InternalAccountTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="homenest-auth-test-")
        self.addCleanup(self.directory.cleanup)
        self.store = AccountStore(Path(self.directory.name) / "accounts.sqlite3")
        self.store_patch = patch("api.AccountStore", return_value=self.store)
        self.store_patch.start()
        self.addCleanup(self.store_patch.stop)
        self.secret_patch = patch.dict(os.environ, {
            "AUTH_JWT_SECRET": "test-only-signing-key-with-more-than-32-bytes",
        })
        self.secret_patch.start()
        self.addCleanup(self.secret_patch.stop)
        self.client = TestClient(api.app)
        self.addCleanup(self.client.close)

    def test_register_hashes_password_and_assigns_user_role(self):
        response = self.client.post("/api/v1/auth/register", json={
            "email": "  Person@Example.test ",
            "full_name": "Example Person",
            "password": "Very-Long-Password-123",
            "role": "Admin",
        })
        self.assertEqual(response.status_code, 201)
        account = response.json()
        self.assertEqual(account["email"], "person@example.test")
        self.assertEqual(account["role"], "User")
        self.assertEqual(account["token_type"], "bearer")
        self.assertNotIn("password", account)
        self.assertEqual(self.store.authenticate(
            "person@example.test", "Very-Long-Password-123")["role"], "User")
        with self.store._connection() as connection:
            stored = connection.execute(
                "SELECT password_salt, password_hash FROM accounts",
            ).fetchone()
        self.assertNotEqual(stored["password_hash"], b"Very-Long-Password-123")
        self.assertEqual(len(stored["password_salt"]), 16)

    def test_provisioned_roles_are_returned_and_enforced_after_login(self):
        for role in ("Manager", "Admin"):
            self.store.create_account(
                f"{role.casefold()}@example.test",
                f"{role} Account",
                "Long-Enough-Password-123",
                role,
            )
            response = self.client.post("/api/v1/auth/login", json={
                "email": f"{role.casefold()}@example.test",
                "password": "Long-Enough-Password-123",
            })
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["role"], role)
            headers = {
                "Authorization": f"Bearer {response.json()['access_token']}",
            }
            self.assertEqual(self.client.get(
                "/api/v1/model", headers=headers,
            ).status_code, 200 if role == "Manager" else 403)

    def test_registration_rejects_duplicate_email_and_weak_password(self):
        payload = {
            "email": "user@example.test",
            "full_name": "Example User",
            "password": "Long-Enough-Password-123",
        }
        self.assertEqual(self.client.post("/api/v1/auth/register", json=payload).status_code, 201)
        duplicate = self.client.post("/api/v1/auth/register", json={
            **payload, "email": "  USER@EXAMPLE.TEST ",
        })
        self.assertEqual(duplicate.status_code, 409)
        with self.store._connection() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM accounts WHERE email = ?",
                ("user@example.test",),
            ).fetchone()[0], 1)
        weak = self.client.post("/api/v1/auth/register", json={
            **payload, "email": "new@example.test", "password": "short",
        })
        self.assertEqual(weak.status_code, 422)

    def test_public_registration_cannot_duplicate_a_provisioned_account(self):
        self.store.create_account(
            "admin@example.test", "Existing Admin",
            "Long-Enough-Password-123", "Admin",
        )
        response = self.client.post("/api/v1/auth/register", json={
            "email": "ADMIN@EXAMPLE.TEST",
            "full_name": "Duplicate User",
            "password": "Another-Password-123",
        })
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.store.get_user("admin@example.test")["role"], "Admin")

    def test_registration_does_not_create_account_without_signing_secret(self):
        with patch.dict(os.environ, {"AUTH_JWT_SECRET": ""}):
            response = self.client.post("/api/v1/auth/register", json={
                "email": "not-created@example.test",
                "full_name": "Not Created",
                "password": "Long-Enough-Password-123",
            })
        self.assertEqual(response.status_code, 503)
        self.assertIsNone(self.store.get_user("not-created@example.test"))

    def test_login_is_generic_and_token_is_role_checked_by_api(self):
        payload = {
            "email": "user@example.test",
            "full_name": "Example User",
            "password": "Long-Enough-Password-123",
        }
        self.client.post("/api/v1/auth/register", json=payload)
        wrong = self.client.post("/api/v1/auth/login", json={
            "email": payload["email"], "password": "Wrong-Password-123",
        })
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(wrong.json()["detail"], "Email hoặc mật khẩu không chính xác.")
        logged_in = self.client.post("/api/v1/auth/login", json={
            "email": payload["email"], "password": payload["password"],
        })
        self.assertEqual(logged_in.status_code, 200)
        token = logged_in.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        prediction = self.client.post("/api/v1/valuations", json={
            "location": "Missing", "type": "Căn hộ", "area": 60,
        }, headers=headers)
        self.assertEqual(prediction.status_code, 200)
        self.assertEqual(self.client.post(
            "/api/v1/valuations/batch", json={"subjects": [{
                "location": "Missing", "type": "Căn hộ", "area": 60,
            }]}, headers=headers).status_code, 403)

    def test_disabled_account_invalidates_existing_token(self):
        created = self.client.post("/api/v1/auth/register", json={
            "email": "disabled@example.test",
            "full_name": "Disabled User",
            "password": "Long-Enough-Password-123",
        }).json()
        with self.store._connection() as connection:
            connection.execute(
                "UPDATE accounts SET is_active = 0 WHERE email = ?",
                (created["email"],),
            )
        response = self.client.post("/api/v1/valuations", json={
            "location": "Missing", "type": "Căn hộ", "area": 60,
        }, headers={"Authorization": f"Bearer {created['access_token']}"})
        self.assertEqual(response.status_code, 401)


class AdminAccountTests(unittest.TestCase):
    password = 'Initial-Password-123'

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='homenest-admin-account-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = AccountStore(self.root / 'accounts.sqlite3')
        for patcher in (
            patch.object(api, 'AccountStore', return_value=self.store),
            patch.object(auth_store, 'PBKDF2_ITERATIONS', 1000),
            patch.dict(os.environ, {'AUTH_JWT_SECRET': 'test-only-signing-key-with-more-than-32-bytes'}),
            patch.multiple(storage, DATA_FILE=self.root / 'properties.json',
                           USERS_FILE=self.root / 'users.json',
                           MESSAGES_FILE=self.root / 'messages.json',
                           FORUM_FILE=self.root / 'forum.json'),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        storage.save_properties([])
        storage.save_users({})
        storage.save_messages([])
        storage.save_forum_posts([])
        self.client = TestClient(api.app)
        self.addCleanup(self.client.close)
        self.headers_by_role = {}
        for role in ('Admin', 'Manager', 'User'):
            email = f'{role.lower()}@example.test'
            self.store.create_account(email, role, self.password, role)
            result = self.login(email)
            self.headers_by_role[role] = {'Authorization': f'Bearer {result.json()["access_token"]}'}
        self.admin = self.headers_by_role['Admin']
        self.email = 'user@example.test'

    def login(self, email, password=None):
        return self.client.post('/api/v1/auth/login',
                                json={'email': email, 'password': password or self.password})

    def target_path(self, suffix=''):
        return f'/api/v1/admin/users/{self.email}{suffix}'

    def test_admin_only_and_cannot_manage_privileged_accounts_or_assign_roles(self):
        for headers in ({}, self.headers_by_role['User'], self.headers_by_role['Manager']):
            expected = 401 if not headers else 403
            self.assertEqual(self.client.get('/api/v1/admin/users', headers=headers).status_code, expected)
            self.assertEqual(self.client.get(self.target_path(), headers=headers).status_code, expected)
            self.assertEqual(self.client.post(self.target_path('/status'), headers=headers,
                                             json={'is_active': False, 'reason': 'Review'}).status_code, expected)
        listed = self.client.get('/api/v1/admin/users', headers=self.admin).json()['users']
        self.assertEqual([user['email'] for user in listed], [self.email])
        for role in ('admin', 'manager'):
            base = f'/api/v1/admin/users/{role}@example.test'
            self.assertEqual(self.client.get(base, headers=self.admin).status_code, 403)
            for suffix, payload in (
                ('/update', {'full_name': 'Changed'}),
                ('/status', {'is_active': False, 'reason': 'Review'}),
                ('/password', {'password': 'New-Password-123', 'reason': 'Recovery'}),
            ):
                self.assertEqual(self.client.post(base + suffix, json=payload,
                                                 headers=self.admin).status_code, 403)
        create = {'email': 'new@example.test', 'full_name': 'New Person', 'password': self.password}
        self.assertEqual(self.client.post('/api/v1/admin/users', headers=self.admin,
                                         json={**create, 'role': 'Admin'}).status_code, 422)
        for forbidden in ({'role': 'Admin'}, {'email': 'changed@example.test'}):
            self.assertEqual(self.client.post(self.target_path('/update'), headers=self.admin,
                                             json={'full_name': 'New Name', **forbidden}).status_code, 422)

    def test_create_update_activity_and_audit_exclude_credentials(self):
        created = self.client.post('/api/v1/admin/users', headers=self.admin, json={
            'email': '  New@Example.test ', 'full_name': 'New Person', 'password': self.password,
        })
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()['user']['role'], 'User')
        self.assertEqual(created.json()['user']['email'], 'new@example.test')
        storage.save_users({self.email: {'full_name': 'Old', 'favorites': [1, 2], 'extra': 'preserve'}})
        storage.save_properties([
            {'id': 1, 'title': 'Sale', 'user_id': self.email, 'status': 'Bán', 'private_secret': 'hidden'},
            {'id': 2, 'title': 'Rent', 'user_id': self.email, 'status': 'Cho thuê'},
            {'id': 3, 'title': 'Other', 'user_id': 'someone@example.test', 'status': 'Bán'},
        ])
        storage.save_forum_posts([
            {'id': 'p', 'author_id': self.email, 'comments': [{'author_id': self.email}, {'author_id': 'other'}]},
        ])
        storage.save_messages([{'from': self.email, 'to': 'support'}, {'from': self.email, 'to': 'other'}])
        updated = self.client.post(self.target_path('/update'), headers=self.admin, json={
            'full_name': ' New Name ', 'phone': '0912345678', 'bio': 'Description', 'admin_note': 'Verify later',
        })
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(self.store.get_user(self.email)['full_name'], 'New Name')
        self.assertEqual(storage.load_users()[self.email]['favorites'], [1, 2])
        self.assertEqual(storage.load_users()[self.email]['extra'], 'preserve')
        detail = self.client.get(self.target_path(), headers=self.admin).json()
        self.assertEqual(detail['profile'], {'email': self.email, 'full_name': 'New Name',
                                             'phone': '0912345678', 'bio': 'Description'})
        self.assertEqual(detail['activity'], {'listing_count': 2, 'sale_count': 1, 'rent_count': 1,
                                              'favorite_count': 2, 'forum_post_count': 1,
                                              'forum_comment_count': 1, 'support_request_count': 1})
        self.assertEqual(detail['audit'][0]['actor'], 'admin@example.test')
        self.assertEqual(detail['audit'][0]['action'], 'update')
        self.assertIn('+00:00', detail['audit'][0]['created_at'])
        serialized = json.dumps(detail)
        for secret in ('password_hash', 'password_salt', 'session_version', self.password, 'hidden'):
            self.assertNotIn(secret, serialized)
        self.assertNotIn('admin_note', self.client.get('/api/v1/auth/me',
                                                      headers=self.headers_by_role['User']).json()['user'])
        canonical = self.client.get('/api/v1/auth/me', headers=self.headers_by_role['User']).json()['user']
        self.assertEqual(canonical['full_name'], 'New Name')
        self.assertTrue(canonical['last_login_at'])

    def test_status_requires_reason_and_reactivation_does_not_restore_old_token(self):
        path = self.target_path('/status')
        for payload in ({'is_active': False}, {'is_active': False, 'reason': '  '}, {'is_active': 'false'}):
            self.assertEqual(self.client.post(path, headers=self.admin, json=payload).status_code, 422)
        disabled = self.client.post(path, headers=self.admin, json={'is_active': False, 'reason': 'Policy review'})
        self.assertEqual(disabled.status_code, 200)
        self.assertFalse(disabled.json()['user']['is_active'])
        self.assertEqual(disabled.json()['user']['disabled_reason'], 'Policy review')
        self.assertEqual(self.login(self.email).status_code, 401)
        old_headers = self.headers_by_role['User']
        self.assertEqual(self.client.get('/api/v1/auth/me', headers=old_headers).status_code, 401)
        self.assertEqual(self.client.post(path, headers=self.admin,
                                         json={'is_active': True, 'reason': 'Reviewed'}).status_code, 200)
        self.assertEqual(self.client.get('/api/v1/auth/me', headers=old_headers).status_code, 401)
        signed_in = self.login(self.email)
        self.assertEqual(signed_in.status_code, 200)
        new_headers = {'Authorization': f'Bearer {signed_in.json()["access_token"]}'}
        self.assertEqual(self.client.get('/api/v1/auth/me', headers=new_headers).status_code, 200)
        self.assertEqual(self.store.audit_history(self.email)[0]['action'], 'activate')

    def test_password_reset_validates_password_and_revokes_existing_sessions(self):
        path = self.target_path('/password')
        for payload in ({'password': 'short', 'reason': 'Recovery'},
                        {'password': ' New-Password-123', 'reason': 'Recovery'},
                        {'password': 'New-Password-123', 'reason': ' '}):
            self.assertEqual(self.client.post(path, headers=self.admin, json=payload).status_code, 422)
        new_password = 'Changed-Password-456'
        changed = self.client.post(path, headers=self.admin,
                                    json={'password': new_password, 'reason': 'Account recovery'})
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(self.login(self.email).status_code, 401)
        self.assertEqual(self.login(self.email, new_password).status_code, 200)
        self.assertEqual(self.client.get('/api/v1/auth/me',
                                         headers=self.headers_by_role['User']).status_code, 401)
        audit = self.store.audit_history(self.email)
        self.assertEqual(audit[0]['action'], 'reset_password')
        self.assertNotIn(new_password, json.dumps(audit))
        self.assertEqual(audit[0]['reason'], 'Account recovery')
        self.assertNotIn('password_hash', json.dumps(changed.json()))

    def test_account_revocation_racing_with_password_verification_cannot_issue_new_session(self):
        original_hash = auth_store._hash_password

        def revoke_during_login(password, salt):
            hashed = original_hash(password, salt)
            self.store.set_user_status(self.email, False, 'Concurrent review', actor='admin@example.test')
            self.store.set_user_status(self.email, True, 'Review completed', actor='admin@example.test')
            return hashed

        with patch.object(auth_store, '_hash_password', side_effect=revoke_during_login):
            self.assertEqual(self.login(self.email).status_code, 401)
        self.assertEqual(self.login(self.email).status_code, 200)

    def test_missing_user_validation_limits_and_duplicate_creation(self):
        self.assertEqual(self.client.get('/api/v1/admin/users/missing@example.test',
                                         headers=self.admin).status_code, 404)
        for payload in ({'full_name': '  '}, {'full_name': 'Name', 'phone': '1' * 31},
                        {'full_name': 'Name', 'bio': 'a' * 2001},
                        {'full_name': 'Name', 'admin_note': 'a' * 2001}):
            self.assertEqual(self.client.post(self.target_path('/update'), headers=self.admin,
                                             json=payload).status_code, 422)
        duplicate = self.client.post('/api/v1/admin/users', headers=self.admin, json={
            'email': self.email, 'full_name': 'Duplicate', 'password': self.password,
        })
        self.assertEqual(duplicate.status_code, 409)

    def test_legacy_database_migration_and_tokens_without_session_version(self):
        from joserfc import jwt
        from joserfc.jwk import OctKey
        legacy_path = self.root / 'legacy.sqlite3'
        salt = b'legacy-salt-12345'
        hashed = auth_store._hash_password(self.password, salt)
        with sqlite3.connect(legacy_path) as connection:
            connection.execute('''CREATE TABLE accounts (
                email TEXT PRIMARY KEY COLLATE NOCASE, full_name TEXT NOT NULL,
                password_salt BLOB NOT NULL, password_hash BLOB NOT NULL,
                role TEXT NOT NULL DEFAULT 'User', is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
            connection.execute('INSERT INTO accounts (email, full_name, password_salt, password_hash) '
                               'VALUES (?, ?, ?, ?)', ('legacy@example.test', 'Legacy', salt, hashed))
        connection.close()
        migrated = AccountStore(legacy_path)
        self.assertEqual(migrated.authenticate('legacy@example.test', self.password)['full_name'], 'Legacy')
        self.assertEqual(migrated.get_session_version('legacy@example.test'), 0)
        self.assertTrue(migrated.get_user('legacy@example.test')['updated_at'])
        AccountStore(legacy_path)
        token = self.headers_by_role['User']['Authorization'].split(' ', 1)[1]
        payload = _token_payload(token)
        del payload['session_version']
        legacy_token = jwt.encode({'alg': 'HS256'}, payload,
                                  OctKey.import_key(os.environ['AUTH_JWT_SECRET'].encode()))
        headers = {'Authorization': f'Bearer {legacy_token}'}
        self.assertEqual(self.client.get('/api/v1/auth/me', headers=headers).status_code, 200)
        self.store.set_user_status(self.email, False, 'Review', actor='admin@example.test')
        self.store.set_user_status(self.email, True, 'Done', actor='admin@example.test')
        self.assertEqual(self.client.get('/api/v1/auth/me', headers=headers).status_code, 401)

    def test_self_profile_updates_only_own_identity_and_preserves_administrative_fields(self):
        self.store.update_user(self.email, 'User', 'Internal admin note', actor='admin@example.test')
        storage.save_users({self.email: {'favorites': [10, 11], 'email': self.email}})
        payload = {'full_name': ' Updated User ', 'phone': ' 0912345678 ',
                   'bio': ' Updated biography ', 'contact_email': 'contact@example.test'}
        updated = self.client.post('/api/v1/auth/profile', headers=self.headers_by_role['User'], json=payload)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()['user']['full_name'], 'Updated User')
        self.assertEqual(updated.json()['user']['email'], self.email)
        self.assertNotIn('admin_note', updated.json()['user'])
        self.assertEqual(self.store.get_user(self.email)['admin_note'], 'Internal admin note')
        self.assertEqual(storage.load_users()[self.email]['favorites'], [10, 11])
        detail = self.client.get(self.target_path(), headers=self.admin).json()
        self.assertEqual(detail['user']['full_name'], 'Updated User')
        self.assertEqual(detail['profile'], {'full_name': 'Updated User', 'phone': '0912345678',
                                             'bio': 'Updated biography', 'email': 'contact@example.test'})
        self.assertEqual(self.store.get_user('manager@example.test')['full_name'], 'Manager')
        for forbidden in ({'email': 'manager@example.test'}, {'target_email': 'manager@example.test'},
                          {'role': 'Admin'}, {'admin_note': 'User edited note'}):
            response = self.client.post('/api/v1/auth/profile', headers=self.headers_by_role['User'],
                                         json={**payload, **forbidden})
            self.assertEqual(response.status_code, 422)
        self.assertIsNone(self.store.authenticate('contact@example.test', self.password))
        self.assertIsNotNone(self.store.authenticate(self.email, self.password))
        # An administrative edit of a different field keeps the contact address.
        self.client.post(self.target_path('/update'), headers=self.admin,
                         json={'full_name': 'Updated Again', 'phone': '0999999999'})
        self.assertEqual(storage.load_users()[self.email]['email'], 'contact@example.test')

    def test_self_profile_manager_support_and_admin_denial_and_input_validation(self):
        payload = {'full_name': 'Manager Name', 'phone': '', 'bio': '', 'contact_email': ''}
        self.assertEqual(self.client.post('/api/v1/auth/profile', json=payload).status_code, 401)
        self.assertEqual(self.client.post('/api/v1/auth/profile', headers=self.admin, json=payload).status_code, 403)
        self.assertEqual(self.client.post('/api/v1/auth/profile', headers=self.headers_by_role['Manager'],
                                         json=payload).status_code, 200)
        self.assertEqual(self.store.get_user('manager@example.test')['full_name'], 'Manager Name')
        for invalid in ({'full_name': '   '}, {'contact_email': 'invalid-email'},
                        {'phone': '0' * 31}, {'bio': 'x' * 2001}):
            self.assertEqual(self.client.post('/api/v1/auth/profile', headers=self.headers_by_role['User'],
                                             json={**payload, **invalid}).status_code, 422)

    def test_profile_storage_failure_rolls_back_database_name_and_audit(self):
        with patch.object(api, 'save_users', side_effect=OSError('Disk unavailable')):
            for path, headers in (('/api/v1/auth/profile', self.headers_by_role['User']),
                                  (self.target_path('/update'), self.admin)):
                response = self.client.post(path, headers=headers, json={'full_name': 'Unsaved Name'})
                self.assertEqual(response.status_code, 503)
                self.assertEqual(self.store.get_user(self.email)['full_name'], 'User')
                self.assertEqual(self.store.audit_history(self.email), [])

    def test_admin_client_round_trip_for_email_containing_slash_and_plus(self):
        email = 'sales/team+tag@example.test'
        token = self.admin['Authorization'].split(' ', 1)[1]
        escaped = '/api/v1/admin/users/sales%2Fteam%2Btag%40example.test'
        called_paths = []

        def transport(method, url, **kwargs):
            path = urlsplit(url).path
            called_paths.append((method, path))
            reply = self.client.request(method, path, json=kwargs.get('json'),
                                        headers=kwargs.get('headers', {}))
            response = requests.Response()
            response.status_code = reply.status_code
            response._content = reply.content
            response.url = url
            return response

        with patch.object(api_client.requests, 'get', side_effect=lambda url, **kw: transport('GET', url, **kw)), \
             patch.object(api_client.requests, 'post', side_effect=lambda url, **kw: transport('POST', url, **kw)):
            created = api_client.admin_create_user({'email': email, 'full_name': 'Sales Team',
                                                    'password': self.password}, token)
            self.assertEqual(created['user']['email'], email)
            self.assertEqual(api_client.admin_user_detail(email, token)['user']['email'], email)
            updated = api_client.admin_update_user(email, {'full_name': 'Updated Sales Team'}, token)
            self.assertEqual(updated['user']['full_name'], 'Updated Sales Team')
            self.assertFalse(api_client.admin_set_user_status(email, False, 'Review required', token)['user']['is_active'])
            self.assertEqual(self.login(email).status_code, 401)
            self.assertTrue(api_client.admin_set_user_status(email, True, 'Review complete', token)['user']['is_active'])
            api_client.admin_reset_user_password(email, 'Sales-Reset-Password-123', 'Account recovery', token)
            self.assertEqual(self.login(email).status_code, 401)
            self.assertEqual(self.login(email, 'Sales-Reset-Password-123').status_code, 200)
            self.assertEqual(api_client.admin_user_detail(email, token)['user']['email'], email)

        self.assertIn(('GET', escaped), called_paths)
        for suffix in ('/update', '/status', '/password'):
            self.assertIn(('POST', escaped + suffix), called_paths)


if __name__ == "__main__":
    unittest.main()
