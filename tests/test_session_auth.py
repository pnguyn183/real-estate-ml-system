import time
import unittest
from unittest.mock import Mock, patch

import session_auth
from api_client import ValuationAPIError


class SessionAuthTests(unittest.TestCase):
    def setUp(self):
        self.auth_settings = {
            'redirect_uri': 'https://app.example.test/oauth2callback',
            'cookie_secret': 'test-cookie-secret',
            'client_id': 'homenest-client',
            'client_secret': 'test-client-secret',
            'server_metadata_url': 'https://identity.example.test/.well-known/openid-configuration',
            'expose_tokens': ['id'],
        }
        self.claims = {
            'sub': 'account-123',
            'email': 'manager@example.test',
            'email_verified': True,
            'name': 'Manager Name',
            'groups': ['homenest-manager'],
            'exp': time.time() + 300,
        }
        self.user = Mock()
        self.user.is_logged_in = True
        self.user.to_dict.return_value = self.claims
        self.user.tokens = {'id': 'opaque-oidc-token'}
        self.secrets = {
            'auth': self.auth_settings,
            'roles': {'manager_groups': ['homenest-manager']},
        }
        self.secrets_patch = patch.object(session_auth.st, 'secrets', self.secrets)
        self.secrets_patch.start()
        self.addCleanup(self.secrets_patch.stop)
        self.user_patch = patch.object(session_auth.st, 'user', self.user)
        self.user_patch.start()
        self.addCleanup(self.user_patch.stop)
        self.state = {}
        state_patch = patch.object(session_auth.st, 'session_state', self.state)
        state_patch.start()
        self.addCleanup(state_patch.stop)

    def test_oidc_identity_and_id_token_are_read_from_framework_session(self):
        self.assertTrue(session_auth.oidc_auth_configured())
        self.assertEqual(session_auth.current_identity(), {
            'subject': 'account-123',
            'username': 'account-123',
            'role': 'Manager',
            'display_name': 'Manager Name',
        })
        self.assertEqual(session_auth.api_bearer_token(), 'opaque-oidc-token')

    def test_missing_exposed_id_token_is_reported(self):
        self.secrets['auth']['expose_tokens'] = []
        self.user.tokens = {}
        with self.assertRaisesRegex(RuntimeError, 'token OIDC'):
            session_auth.api_bearer_token()

    def test_expired_oidc_identity_is_logged_out(self):
        self.claims['exp'] = time.time() - 1
        with patch.object(session_auth.st, 'logout') as logout:
            self.assertIsNone(session_auth.current_identity())
        logout.assert_called_once_with()

    def internal_session(self):
        self.state.update(auth_mode='internal', auth_token='opaque-internal-token',
                          auth_expires_at=time.time() + 300, username='user@example.test',
                          display_name='Cached Name', role='Admin', logged_in=True,
                          favorites=[1], admin_password='sensitive form data')

    def test_internal_identity_is_revalidated_on_every_rerun_and_refreshes_role(self):
        self.internal_session()
        canonical = {'user': {'email': 'user@example.test', 'full_name': 'New Name',
                              'role': 'User', 'is_active': True}}
        with patch.object(session_auth, 'get_account_identity', return_value=canonical) as verify:
            for _ in range(2):
                self.assertEqual(session_auth.current_identity(), {
                    'subject': 'user@example.test', 'username': 'user@example.test',
                    'role': 'User', 'display_name': 'New Name',
                })
        self.assertEqual(verify.call_count, 2)
        verify.assert_called_with('opaque-internal-token')

    def test_locked_revoked_or_offline_sessions_clear_all_cached_data(self):
        for failure in ('Tài khoản đã bị khóa.', 'Phiên đã bị thu hồi.', 'Không kết nối được backend.'):
            self.internal_session()
            with patch.object(session_auth, 'get_account_identity', side_effect=ValuationAPIError(failure)):
                self.assertIsNone(session_auth.current_identity())
            self.assertEqual(set(self.state), {'auth_notice', 'public_page', 'auth_screen'})
            self.assertIn(failure, self.state['auth_notice'])
            self.assertEqual(self.state['public_page'], 'auth')
            self.assertEqual(self.state['auth_screen'], 'login')

    def test_expired_internal_session_is_cleared_without_an_api_call(self):
        self.internal_session()
        self.state['auth_expires_at'] = time.time() - 1
        with patch.object(session_auth, 'get_account_identity') as verify:
            self.assertIsNone(session_auth.current_identity())
        verify.assert_not_called()
        self.assertEqual(set(self.state), {'auth_notice', 'public_page', 'auth_screen'})

    def test_invalid_backend_identity_does_not_authorize_cached_role(self):
        for user in ({}, {'email': 'user@example.test', 'role': 'Admin', 'is_active': False},
                     {'email': 'user@example.test', 'role': 'Unknown', 'is_active': True}):
            self.internal_session()
            with patch.object(session_auth, 'get_account_identity', return_value={'user': user}):
                self.assertIsNone(session_auth.current_identity())
            self.assertNotIn('role', self.state)
