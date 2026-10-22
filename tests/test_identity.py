import os
import time
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from joserfc import jwt
from joserfc.jwk import RSAKey

from identity import resolve_role
from oidc import verify_id_token


class IdentityTests(unittest.TestCase):
    def test_role_mapping_requires_verified_emails_and_matches_provider_groups(self):
        options = {
            'admin_groups': ['property-admins'],
            'manager_groups': ['property-managers'],
            'admin_emails': ['admin@example.test'],
            'manager_emails': ['manager@example.test'],
        }
        self.assertEqual(resolve_role({'groups': ['property-admins']}, **options), 'Admin')
        self.assertEqual(resolve_role({'roles': ['property-managers']}, **options), 'Manager')
        self.assertEqual(resolve_role({
            'email': 'admin@example.test', 'email_verified': False,
        }, **options), 'User')
        self.assertEqual(resolve_role({
            'email': 'manager@example.test', 'email_verified': True,
        }, **options), 'Manager')
        self.assertEqual(resolve_role({'groups': ['unrelated']}, **options), 'User')

    def test_id_token_signature_issuer_audience_and_expiry_are_checked(self):
        signing_key = RSAKey.generate_key(2048, auto_kid=True)
        other_key = RSAKey.generate_key(2048, auto_kid=True)
        issuer = 'https://identity.example.test'
        client_id = 'homenest-test'
        jwks = {'keys': [signing_key.as_dict(private=False)]}
        metadata = {'issuer': issuer, 'jwks_uri': issuer + '/keys'}
        env = patch.dict(os.environ, {
            'AUTH_OIDC_ISSUER': issuer,
            'AUTH_OIDC_METADATA_URL': issuer + '/.well-known/openid-configuration',
            'AUTH_OIDC_CLIENT_ID': client_id,
        })
        env.start()
        self.addCleanup(env.stop)

        def mock_json(url):
            return metadata if url.endswith('openid-configuration') else jwks

        lookup = patch('oidc._get_json', side_effect=mock_json)
        lookup.start()
        self.addCleanup(lookup.stop)

        def make_token(key, **overrides):
            claims = {
                'iss': issuer,
                'aud': client_id,
                'sub': 'subject-123',
                'iat': time.time(),
                'exp': time.time() + 300,
                **overrides,
            }
            return jwt.encode({'alg': 'RS256', 'kid': key.kid}, claims, key)

        self.assertEqual(verify_id_token(make_token(signing_key))['sub'], 'subject-123')
        for token in (
            make_token(other_key),
            make_token(signing_key, iss='https://other.example.test'),
            make_token(signing_key, aud='another-client'),
            make_token(signing_key, exp=time.time() - 1),
        ):
            with self.subTest(token=token[:20]), self.assertRaises(HTTPException):
                verify_id_token(token)
