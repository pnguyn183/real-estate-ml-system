"""Refresh persistence uses revocable opaque handles with an absolute expiry."""

import hashlib
import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import requests
from fastapi import HTTPException
from fastapi.testclient import TestClient

import api
import api_client
import auth_store
from auth_store import AccountStore
from internal_auth import _token_payload, issue_token


class BrowserSessionTests(unittest.TestCase):
    password = 'Browser-Session-Password-123'

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='homenest-browser-session-test-')
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'accounts.sqlite3'
        self.store = AccountStore(self.path)
        for patcher in (
            patch.object(api, 'AccountStore', return_value=self.store),
            patch.object(auth_store, 'PBKDF2_ITERATIONS', 1000),
            patch.dict(os.environ, {'AUTH_JWT_SECRET': 'test-only-browser-signing-key-more-than-32-bytes'}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(api.app)
        self.addCleanup(self.client.close)
        self.accounts = {}
        for role in ('User', 'Manager', 'Admin'):
            email = f'{role.lower()}@example.test'
            self.store.create_account(email, role, self.password, role)
            self.accounts[role] = self.client.post('/api/v1/auth/login', json={
                'email': email, 'password': self.password,
            }).json()

    def create(self, role='User', token=None):
        token = token or self.accounts[role]['access_token']
        return self.client.post('/api/v1/auth/browser-session', json={},
                                headers={'Authorization': f'Bearer {token}'})

    def restore(self, handle):
        return self.client.post('/api/v1/auth/browser-session/restore',
                                json={'browser_session': handle})

    def test_persists_only_digest_and_safe_account_identity_for_all_roles(self):
        for role, original in self.accounts.items():
            self.store.update_user('user@example.test', 'User', 'Private management note', actor='Admin')
            created = self.create(role)
            self.assertEqual(created.status_code, 200)
            browser = created.json()
            handle = browser['browser_session']
            self.assertEqual(len(handle), 43)
            self.assertEqual(browser['expires_at'], original['expires_at'])
            restored = self.restore(handle)
            self.assertEqual(restored.status_code, 200)
            account = restored.json()
            self.assertEqual(account['email'], original['email'])
            self.assertEqual(account['role'], role)
            self.assertEqual(account['expires_at'], original['expires_at'])
            self.assertEqual(_token_payload(account['access_token'])['exp'], browser['expires_at'])
            self.assertEqual(self.client.get('/api/v1/auth/me', headers={
                'Authorization': f'Bearer {account["access_token"]}',
            }).status_code, 200)
            with self.store._connection() as connection:
                row = dict(connection.execute('SELECT * FROM browser_sessions WHERE email = ?',
                                              (original['email'],)).fetchone())
            self.assertEqual(row['handle_hash'], hashlib.sha256(handle.encode('ascii')).hexdigest())
            self.assertEqual(set(row), {'handle_hash', 'email', 'session_version', 'expires_at', 'created_at'})
            for secret in (handle, self.password, original['access_token'], account['access_token']):
                self.assertNotIn(secret, json.dumps(row))
            for secret in ('admin_note', 'password_hash', 'password_salt', 'session_version', 'Private management note'):
                self.assertNotIn(secret, json.dumps(account))

    def test_restore_does_not_slide_expiry_or_create_more_handles(self):
        now = int(time.time())
        user = self.store.get_user('user@example.test')
        original = issue_token(user, expires_at=now + 60)
        browser = self.create(token=original['access_token']).json()
        with patch('auth_store.time.time', return_value=now + 40):
            restored = self.restore(browser['browser_session'])
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored.json()['expires_at'], now + 60)
        self.assertEqual(_token_payload(restored.json()['access_token'])['exp'], now + 60)
        with self.store._connection() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM browser_sessions').fetchone()[0], 1)
        with patch('auth_store.time.time', return_value=now + 60):
            self.assertEqual(self.restore(browser['browser_session']).status_code, 401)
            with self.assertRaises(ValueError):
                self.store.create_browser_session(user['email'], 0, now + 60)

    def test_revoke_is_idempotent_and_only_revokes_its_handle(self):
        first = self.create().json()['browser_session']
        second = self.create().json()['browser_session']
        for _ in range(2):
            revoked = self.client.post('/api/v1/auth/browser-session/revoke',
                                       json={'browser_session': first})
            self.assertEqual(revoked.status_code, 200)
            self.assertEqual(revoked.json(), {'revoked': True})
        self.assertEqual(self.restore(first).status_code, 401)
        self.assertEqual(self.restore(second).status_code, 200)

    def test_lock_unlock_and_password_reset_do_not_revive_old_handles(self):
        email = 'user@example.test'
        handle = self.create().json()['browser_session']
        self.store.set_user_status(email, False, 'Account review', actor='Admin')
        self.assertEqual(self.restore(handle).status_code, 401)
        self.store.set_user_status(email, True, 'Reviewed', actor='Admin')
        self.assertEqual(self.restore(handle).status_code, 401)
        new_login = self.client.post('/api/v1/auth/login', json={'email': email, 'password': self.password}).json()
        new_handle = self.create(token=new_login['access_token']).json()['browser_session']
        self.assertEqual(self.restore(new_handle).status_code, 200)
        self.store.reset_user_password(email, 'Changed-Password-456', 'Account recovery', actor='Admin')
        self.assertEqual(self.restore(new_handle).status_code, 401)
        self.assertEqual(self.create(token=new_login['access_token']).status_code, 401)

    def test_create_cannot_use_version_replaced_after_token_verification(self):
        original_create = self.store.create_browser_session

        def revoke_then_create(email, version, expires_at):
            self.store.reset_user_password(email, 'Changed-Password-456', 'Recovery', actor='Admin')
            return original_create(email, version, expires_at)

        with patch.object(self.store, 'create_browser_session', side_effect=revoke_then_create):
            self.assertEqual(self.create().status_code, 401)
        with self.store._connection() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM browser_sessions').fetchone()[0], 0)

    def test_restore_uses_current_account_role_and_rejects_missing_account(self):
        handle = self.create().json()['browser_session']
        with self.store._connection() as connection:
            connection.execute("UPDATE accounts SET role = 'Manager' WHERE email = 'user@example.test'")
        self.assertEqual(self.restore(handle).json()['role'], 'Manager')
        with self.store._connection() as connection:
            connection.execute("DELETE FROM accounts WHERE email = 'user@example.test'")
        self.assertEqual(self.restore(handle).status_code, 401)

    def test_malformed_and_unknown_handles_fail_with_same_generic_unauthorized(self):
        generic = None
        for handle in (None, '', 'short', 'x' * 42, 'x' * 44, 'x' * 43, 'x' * 10000,
                       '/' * 43, 'é' * 43, 123, True, [], {}):
            response = self.restore(handle)
            self.assertEqual(response.status_code, 401)
            generic = generic or response.json()
            self.assertEqual(response.json(), generic)
        missing = self.client.post('/api/v1/auth/browser-session/restore', json={})
        self.assertEqual(missing.status_code, 401)
        self.assertEqual(missing.json(), generic)
        for invalid in (None, '', 123, '!' * 43):
            self.assertEqual(self.client.post('/api/v1/auth/browser-session/revoke',
                                             json={'browser_session': invalid}).status_code, 401)
        with patch.object(api, 'verify_id_token', side_effect=HTTPException(401, 'Invalid token')):
            self.assertEqual(self.create(token='invalid-token').status_code, 401)
        self.assertEqual(self.client.post('/api/v1/auth/browser-session', json={}).status_code, 401)

    def test_oidc_sessions_cannot_create_internal_browser_credentials(self):
        with patch.object(api, 'verify_id_token', return_value={
            'sub': 'oidc-user', 'email': 'user@example.test', 'iss': 'https://identity.example.test',
        }):
            self.assertEqual(self.create(token='oidc-token').status_code, 403)

    def test_creation_bounds_live_sessions_without_clobbering_accounts(self):
        first = self.create().json()['browser_session']
        for _ in range(auth_store._MAX_BROWSER_SESSIONS):
            latest = self.create().json()['browser_session']
        self.assertEqual(self.restore(first).status_code, 401)
        self.assertEqual(self.restore(latest).status_code, 200)
        with self.store._connection() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM browser_sessions').fetchone()[0],
                             auth_store._MAX_BROWSER_SESSIONS)
        reopened = AccountStore(self.path)
        self.assertEqual(reopened.restore_browser_session(latest)[0]['role'], 'User')
        for role in self.accounts:
            self.assertEqual(reopened.get_user(f'{role.lower()}@example.test')['role'], role)
            self.assertIsNotNone(reopened.authenticate(f'{role.lower()}@example.test', self.password))

    def test_additive_migration_preserves_legacy_credentials_and_privileged_role(self):
        legacy_path = self.path.with_name('legacy.sqlite3')
        salt = b'legacy-salt-12345'
        password_hash = auth_store._hash_password(self.password, salt)
        with sqlite3.connect(legacy_path) as connection:
            connection.execute('''CREATE TABLE accounts (
                email TEXT PRIMARY KEY COLLATE NOCASE, full_name TEXT NOT NULL,
                password_salt BLOB NOT NULL, password_hash BLOB NOT NULL,
                role TEXT NOT NULL DEFAULT 'User', is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
            connection.execute('INSERT INTO accounts (email, full_name, password_salt, password_hash, role) '
                               'VALUES (?, ?, ?, ?, ?)',
                               ('legacy@example.test', 'Legacy Manager', salt, password_hash, 'Manager'))
        connection.close()
        migrated = AccountStore(legacy_path)
        self.assertEqual(migrated.authenticate('legacy@example.test', self.password)['role'], 'Manager')
        browser = migrated.create_browser_session('legacy@example.test', 0, int(time.time()) + 60)
        self.assertEqual(migrated.restore_browser_session(browser['browser_session'])[0]['role'], 'Manager')
        with migrated._connection() as connection:
            account = connection.execute('SELECT * FROM accounts').fetchone()
        self.assertEqual(account['password_salt'], salt)
        self.assertEqual(account['password_hash'], password_hash)

    def test_client_helpers_keep_handle_in_post_body_and_require_bearer_for_create(self):
        original = self.accounts['User']
        called = []

        def transport(url, **kwargs):
            from urllib.parse import urlsplit
            path = urlsplit(url).path
            called.append((path, kwargs))
            reply = self.client.post(path, json=kwargs.get('json'), headers=kwargs.get('headers', {}))
            response = requests.Response()
            response.status_code = reply.status_code
            response._content = reply.content
            response.url = url
            return response

        with patch.object(api_client.requests, 'post', side_effect=transport):
            browser = api_client.create_browser_session(original['access_token'])
            self.assertEqual(api_client.restore_browser_session(browser['browser_session'])['email'], original['email'])
            self.assertTrue(api_client.revoke_browser_session(browser['browser_session'])['revoked'])
            with self.assertRaises(api_client.ValuationAPIError):
                api_client.restore_browser_session(browser['browser_session'])
        self.assertEqual(called[0][1]['headers']['Authorization'], f'Bearer {original["access_token"]}')
        for path, kwargs in called[1:]:
            self.assertNotIn('?', path)
            self.assertNotIn(browser['browser_session'], path)
            self.assertEqual(kwargs['json'], {'browser_session': browser['browser_session']})
            self.assertNotIn('headers', kwargs)


if __name__ == '__main__':
    unittest.main()
