import os
import unittest
from unittest.mock import patch

import requests
from fastapi.testclient import TestClient
from api import app
from api_client import _error_message, predict, ValuationAPIError
from storage import DataStoreError


class APITests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        env = patch.dict(os.environ, {
            'AUTH_OIDC_ISSUER': 'https://identity.example.test',
            'AUTH_OIDC_CLIENT_ID': 'homenest-client',
            'AUTH_ADMIN_GROUPS': 'homenest-admin',
            'AUTH_MANAGER_GROUPS': 'homenest-manager',
        })
        env.start()
        self.addCleanup(env.stop)
        verifier = patch('api.verify_id_token', side_effect=self.verify_test_token)
        verifier.start()
        self.addCleanup(verifier.stop)
        self.subject = {'location': 'Test', 'type': 'Căn hộ', 'area': 20}
        records = [{'title': 'A', 'location': 'Test', 'type': 'Căn hộ',
                    'status': 'Bán', 'area': 10, 'price': 1000}]
        data = patch('api.load_properties', return_value=records)
        data.start()
        self.addCleanup(data.stop)

    @staticmethod
    def verify_test_token(token):
        roles = {
            'user-token': [],
            'manager-token': ['homenest-manager'],
            'admin-token': ['homenest-admin'],
        }
        if token not in roles:
            from fastapi import HTTPException
            raise HTTPException(401, 'Invalid test token')
        return {
            'sub': token,
            'iss': 'https://identity.example.test',
            'aud': 'homenest-client',
            'groups': roles[token],
        }

    @staticmethod
    def headers(token='user-token'):
        return {'Authorization': f'Bearer {token}'}

    def test_health_and_median(self):
        health = self.client.get('/health')
        self.assertEqual(health.status_code, 200)
        self.assertTrue(health.json()['estimator_ready'])
        self.assertEqual(health.json()['comparable_listing_count'], 1)
        response = self.client.post('/api/v1/valuations', json=self.subject,
                                    headers=self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['price'], 2000)
        self.assertEqual(self.client.post('/api/v1/valuations', json=self.subject).status_code, 401)
        self.assertEqual(self.client.post(
            '/api/v1/valuations', json=self.subject,
            headers={'X-API-Key': 'legacy-shared-key'}).status_code, 401)

    def test_data_store_errors_are_reported_as_actionable_service_unavailable(self):
        message = 'File dữ liệu properties.json không phải JSON hợp lệ.'
        with patch('api.load_properties', side_effect=DataStoreError(message)):
            response = self.client.get('/health')

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['detail'], message)
        error = requests.Response()
        error.status_code = response.status_code
        error._content = response.content
        self.assertEqual(_error_message(error), message)

    def test_invalid_and_no_matches(self):
        self.assertEqual(self.client.post('/api/v1/valuations',
                                          json={**self.subject, 'area': -1},
                                          headers=self.headers()).status_code, 422)
        response = self.client.post('/api/v1/valuations',
                                    json={**self.subject, 'location': 'Other'},
                                    headers=self.headers())
        self.assertEqual(response.json()['status'], 'insufficient_data')
        self.assertIsNone(response.json()['price'])

    def test_role_protects_batch_and_model_endpoints(self):
        payload = {'subjects': [self.subject]}
        self.assertEqual(self.client.post('/api/v1/valuations/batch', json=payload,
                                          headers=self.headers()).status_code, 403)
        self.assertEqual(self.client.get('/api/v1/model').status_code, 401)
        self.assertEqual(self.client.get('/api/v1/model',
                                         headers=self.headers()).status_code, 403)
        for role_token, expected in (('manager-token', 200), ('admin-token', 403)):
            headers = self.headers(role_token)
            self.assertEqual(self.client.post('/api/v1/valuations/batch', json=payload,
                                              headers=headers).status_code, expected)
            self.assertEqual(self.client.get('/api/v1/model', headers=headers).status_code, expected)
        self.assertEqual(self.client.post('/api/v1/valuations', json=self.subject,
                                         headers=self.headers('admin-token')).status_code, 403)

    def test_batch_partial_failure_and_limit(self):
        response = self.client.post('/api/v1/valuations/batch',
                                    json={'subjects': [self.subject, {}]},
                                    headers=self.headers('manager-token'))
        rows = response.json()['results']
        self.assertEqual(rows[0]['Giá tham khảo (₫)'], 2000)
        self.assertTrue(rows[1]['Lỗi'])
        self.assertEqual(self.client.post('/api/v1/valuations/batch',
                                          json={'subjects': [self.subject] * 501},
                                          headers=self.headers('manager-token')).status_code, 422)

    def test_connection_error_is_explicit(self):
        import requests
        with patch('api_client.requests.post', side_effect=requests.ConnectionError):
            with self.assertRaises(ValuationAPIError):
                predict(self.subject, 'user-token')

    def test_http_auth_errors_are_not_reported_as_network_failures(self):
        import requests
        response = requests.Response()
        response.status_code = 403
        response._content = b'{"detail":"forbidden"}'
        with patch('api_client.requests.post', return_value=response):
            with self.assertRaisesRegex(ValuationAPIError, 'không có quyền'):
                predict(self.subject, 'user-token')
