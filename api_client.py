"""HTTP boundary for valuation; connection errors never produce local estimates."""
import os
from time import perf_counter
from urllib.parse import quote

import requests


class ValuationAPIError(RuntimeError):
    pass


def _error_message(response):
    status = response.status_code
    try:
        detail = response.json().get('detail')
    except (ValueError, AttributeError):
        detail = None
    if status == 401:
        return 'Phiên đăng nhập không hợp lệ hoặc đã hết hạn. Vui lòng đăng nhập lại.'
    if status == 403:
        return 'Tài khoản của bạn không có quyền thực hiện thao tác này.'
    if status == 422:
        if isinstance(detail, list):
            detail = "; ".join(
                str(item.get("msg", "Dữ liệu không hợp lệ"))
                for item in detail if isinstance(item, dict)
            )
        return f'Dữ liệu gửi tới dịch vụ không hợp lệ: {detail or "hãy kiểm tra các trường nhập"}.'
    if status == 409:
        return str(detail or 'Email này đã được đăng ký.')
    if status == 404:
        return str(detail or 'Không tìm thấy tài khoản người dùng.')
    if status == 503 and isinstance(detail, str):
        return detail
    if status >= 500:
        return 'Dịch vụ HomeNest đang gặp sự cố. Vui lòng thử lại sau.'
    return f'Dịch vụ HomeNest từ chối yêu cầu (HTTP {status}).'


def post(path, payload, access_token):
    base = os.environ.get('VALUATION_API_URL', 'http://127.0.0.1:8000').rstrip('/')
    if not access_token:
        raise ValuationAPIError('Không tìm thấy token đăng nhập OIDC. Vui lòng đăng nhập lại.')
    headers = {'Authorization': f'Bearer {access_token}'}
    started = perf_counter()
    try:
        response = requests.post(base + path, json=payload, headers=headers, timeout=(3, 45))
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError('API response must be a JSON object')
        result['_client_response_time_ms'] = round((perf_counter() - started) * 1000, 1)
        return result
    except requests.HTTPError as exc:
        raise ValuationAPIError(_error_message(exc.response)) from exc
    except (requests.ConnectionError, requests.Timeout) as exc:
        raise ValuationAPIError('Không kết nối được dịch vụ HomeNest. Vui lòng kiểm tra backend và thử lại.') from exc
    except (requests.RequestException, ValueError) as exc:
        raise ValuationAPIError('Dịch vụ HomeNest trả về dữ liệu không hợp lệ. Vui lòng thử lại.') from exc


def health():
    base = os.environ.get('VALUATION_API_URL', 'http://127.0.0.1:8000').rstrip('/')
    started = perf_counter()
    try:
        response = requests.get(base + '/health', timeout=(2, 4))
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError('Health response must be a JSON object')
        result['_client_response_time_ms'] = round((perf_counter() - started) * 1000, 1)
        return result
    except (requests.RequestException, ValueError) as exc:
        raise ValuationAPIError('Không thể kết nối tới trạng thái dịch vụ định giá.') from exc


def predict(subject, access_token):
    return post('/api/v1/valuations', subject, access_token)


def predict_batch(subjects, access_token):
    return post('/api/v1/valuations/batch', {'subjects': subjects}, access_token)


def register_account(email, full_name, password):
    return post_public('/api/v1/auth/register', {
        'email': email,
        'full_name': full_name,
        'password': password,
    })


def login_account(email, password):
    return post_public('/api/v1/auth/login', {
        'email': email,
        'password': password,
    })


def create_browser_session(access_token):
    return post('/api/v1/auth/browser-session', {}, access_token)


def restore_browser_session(handle):
    return post_public('/api/v1/auth/browser-session/restore', {'browser_session': handle})


def revoke_browser_session(handle):
    return post_public('/api/v1/auth/browser-session/revoke', {'browser_session': handle})


def model_info(access_token):
    return get('/api/v1/model', access_token)


def get_account_identity(access_token):
    return get('/api/v1/auth/me', access_token)


def update_account_profile(payload, access_token):
    return post('/api/v1/auth/profile', payload, access_token)


def admin_list_users(access_token):
    return get('/api/v1/admin/users', access_token)


def admin_user_detail(email, access_token):
    return get(f'/api/v1/admin/users/{quote(email, safe="")}', access_token)


def admin_create_user(payload, access_token):
    return post('/api/v1/admin/users', payload, access_token)


def admin_update_user(email, payload, access_token):
    return post(f'/api/v1/admin/users/{quote(email, safe="")}/update', payload, access_token)


def admin_set_user_status(email, is_active, reason, access_token):
    return post(f'/api/v1/admin/users/{quote(email, safe="")}/status',
                {'is_active': is_active, 'reason': reason}, access_token)


def admin_reset_user_password(email, password, reason, access_token):
    return post(f'/api/v1/admin/users/{quote(email, safe="")}/password',
                {'password': password, 'reason': reason}, access_token)


def post_public(path, payload):
    base = os.environ.get('VALUATION_API_URL', 'http://127.0.0.1:8000').rstrip('/')
    try:
        response = requests.post(base + path, json=payload, timeout=(3, 10))
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError('API response must be a JSON object')
        return result
    except requests.HTTPError as exc:
        if path.endswith("/auth/login") and exc.response.status_code == 401:
            raise ValuationAPIError("Email hoặc mật khẩu không chính xác.") from exc
        raise ValuationAPIError(_error_message(exc.response)) from exc
    except (requests.ConnectionError, requests.Timeout) as exc:
        raise ValuationAPIError('Không kết nối được dịch vụ tài khoản. Hãy kiểm tra backend và thử lại.') from exc
    except (requests.RequestException, ValueError) as exc:
        raise ValuationAPIError('Dịch vụ tài khoản trả về dữ liệu không hợp lệ. Vui lòng thử lại.') from exc


def get(path, access_token):
    base = os.environ.get('VALUATION_API_URL', 'http://127.0.0.1:8000').rstrip('/')
    if not access_token:
        raise ValuationAPIError('Không tìm thấy token đăng nhập OIDC. Vui lòng đăng nhập lại.')
    started = perf_counter()
    try:
        response = requests.get(base + path, headers={'Authorization': f'Bearer {access_token}'},
                                timeout=(3, 8))
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError('API response must be a JSON object')
        result['_client_response_time_ms'] = round((perf_counter() - started) * 1000, 1)
        return result
    except requests.HTTPError as exc:
        raise ValuationAPIError(_error_message(exc.response)) from exc
    except (requests.ConnectionError, requests.Timeout) as exc:
        raise ValuationAPIError('Không kết nối được dịch vụ HomeNest. Vui lòng kiểm tra backend và thử lại.') from exc
    except (requests.RequestException, ValueError) as exc:
        raise ValuationAPIError('Dịch vụ HomeNest trả về dữ liệu không hợp lệ. Vui lòng thử lại.') from exc
