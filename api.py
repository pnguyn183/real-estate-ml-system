"""HomeNest valuation API. Catalog stays on the server, never supplied by callers."""
import math
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from starlette.requests import Request

from auth_store import AccountStore, validate_contact_email
from estimator import estimate
from identity import resolve_role
from internal_auth import (ensure_configured, is_internal_token, issue_token,
                           verify_internal_token)
from oidc import role_settings_from_environment, verify_id_token
from prediction import estimate_batch, validate_subject
from storage import (DataStoreError, load_forum_posts, load_messages, load_properties,
                     load_users, save_users)

app = FastAPI(title='HomeNest API', version='1.0.0',
              description='Tài khoản, quản trị người dùng và ước tính giá bất động sản HomeNest.')


@app.exception_handler(DataStoreError)
async def data_store_error_handler(request: Request, error: DataStoreError):
    return JSONResponse(status_code=503, content={'detail': str(error)})


def current_principal(authorization: Annotated[str | None, Header()] = None):
    scheme, _, token = (authorization or '').partition(' ')
    if scheme.casefold() != 'bearer' or not token:
        raise HTTPException(401, 'Cần đăng nhập để sử dụng API.',
                            headers={'WWW-Authenticate': 'Bearer'})
    if is_internal_token(token):
        return verify_internal_token(token, AccountStore())
    claims = verify_id_token(token)
    return {
        **claims,
        'app_role': resolve_role(claims, **role_settings_from_environment()),
    }


def require_roles(principal, *roles):
    if principal['app_role'] not in roles:
        raise HTTPException(403, 'Tài khoản không có quyền thực hiện thao tác này.')


def is_comparable_listing(property_):
    if (property_.get('status') != 'Bán'
            or not str(property_.get('location', '')).strip()
            or not str(property_.get('type', '')).strip()):
        return False
    try:
        price = float(property_['price'])
        area = float(property_['area'])
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(price) and math.isfinite(area) and price > 0 and area > 0


class BatchRequest(BaseModel):
    subjects: list[dict[str, Any]] = Field(min_length=1, max_length=500)


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    full_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=12, max_length=128)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class BrowserSessionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    # Validate all credential shapes uniformly as 401, including missing/null.
    browser_session: Any = None


class AdminCreateUserRequest(RegisterRequest):
    model_config = ConfigDict(extra='forbid')


class AdminUpdateUserRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    full_name: str = Field(min_length=1, max_length=120)
    phone: str = Field(default='', max_length=30)
    bio: str = Field(default='', max_length=2000)
    admin_note: str = Field(default='', max_length=2000)


class AdminStatusRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    is_active: StrictBool
    reason: str = Field(default='', max_length=1000)


class AdminPasswordRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    password: str = Field(min_length=12, max_length=128)
    reason: str = Field(min_length=1, max_length=1000)


class ProfileRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    full_name: str = Field(min_length=1, max_length=120)
    phone: str = Field(default='', max_length=30)
    bio: str = Field(default='', max_length=2000)
    contact_email: str = Field(default='', max_length=254)


def account_error(error):
    if isinstance(error, LookupError):
        return HTTPException(404, str(error))
    if isinstance(error, PermissionError):
        return HTTPException(403, str(error))
    return HTTPException(409 if 'đã được đăng ký' in str(error) else 422, str(error))


def managed_user(store, email):
    user = store.get_user(email)
    if user is None:
        raise HTTPException(404, 'Không tìm thấy tài khoản User này.')
    if user['role'] != 'User':
        raise HTTPException(403, 'Chỉ được quản lý tài khoản có vai trò User.')
    return user


def actor_id(principal):
    return str(principal.get('email') or principal.get('sub') or 'unknown')


def identity_user(user):
    return {key: value for key, value in user.items() if key != 'admin_note'}


@app.get('/health')
def health():
    properties = load_properties()
    comparable_count = sum(is_comparable_listing(property_) for property_ in properties)
    return {
        'status': 'ok',
        'service': 'homenest-valuation',
        'version': '1.0.0',
        'api_ready': True,
        'estimator_ready': True,
        'catalog_ready': bool(properties),
        'comparable_listing_count': comparable_count,
        'method': 'comparable-median-v1',
    }


@app.post('/api/v1/auth/register', status_code=201)
def register(request: RegisterRequest):
    ensure_configured()
    try:
        store = AccountStore()
        user = store.create_user(
            request.email, request.full_name, request.password,
        )
    except ValueError as error:
        status_code = 409 if "đã được đăng ký" in str(error) else 422
        raise HTTPException(status_code, str(error)) from error
    # Newly inserted credentials always start at version zero; concurrent
    # administrative revocation must also invalidate this initial session.
    return {**identity_user(user), **issue_token(user, session_version=0)}


@app.post('/api/v1/auth/login')
def login(request: LoginRequest):
    store = AccountStore()
    authenticated = store.authenticate_session(request.email, request.password)
    if authenticated is None:
        raise HTTPException(401, "Email hoặc mật khẩu không chính xác.",
                            headers={'WWW-Authenticate': 'Bearer'})
    user, session_version = authenticated
    return {**identity_user(user), **issue_token(user, session_version=session_version)}


@app.get('/api/v1/auth/me')
def account_identity(principal: Annotated[dict, Depends(current_principal)]):
    if principal.get('iss') == 'homenest-internal':
        return {'user': identity_user(AccountStore().get_user(principal['email']))}
    return {'user': {
        'email': str(principal.get('email') or principal['sub']),
        'full_name': str(principal.get('name') or principal.get('email') or principal['sub']),
        'role': principal['app_role'],
        'is_active': True,
    }}


@app.post('/api/v1/auth/browser-session')
def create_browser_session(principal: Annotated[dict, Depends(current_principal)]):
    require_roles(principal, 'User', 'Manager', 'Admin')
    if principal.get('iss') != 'homenest-internal':
        raise HTTPException(403, 'Phiên trình duyệt chỉ dành cho tài khoản đăng nhập bằng email/mật khẩu.')
    try:
        return AccountStore().create_browser_session(
            principal['email'], principal.get('session_version', 0), principal['exp'],
        )
    except ValueError as error:
        raise HTTPException(401, 'Phiên đăng nhập không hợp lệ hoặc đã hết hạn.') from error


@app.post('/api/v1/auth/browser-session/restore')
def restore_browser_session(request: BrowserSessionRequest):
    try:
        user, session_version, expires_at = AccountStore().restore_browser_session(request.browser_session)
    except ValueError as error:
        raise HTTPException(401, 'Phiên đăng nhập không hợp lệ hoặc đã hết hạn.') from error
    return {**identity_user(user), **issue_token(user, session_version=session_version, expires_at=expires_at)}


@app.post('/api/v1/auth/browser-session/revoke')
def revoke_browser_session(request: BrowserSessionRequest):
    try:
        AccountStore().revoke_browser_session(request.browser_session)
    except ValueError as error:
        raise HTTPException(401, 'Phiên đăng nhập không hợp lệ hoặc đã hết hạn.') from error
    return {'revoked': True}


@app.post('/api/v1/auth/profile')
def update_account_profile(request: ProfileRequest,
                           principal: Annotated[dict, Depends(current_principal)]):
    require_roles(principal, 'User', 'Manager')
    if principal.get('iss') != 'homenest-internal':
        raise HTTPException(403, 'Hồ sơ này chỉ dành cho tài khoản đăng nhập bằng email/mật khẩu.')
    store = AccountStore()
    email = principal['email']
    try:
        contact_email = validate_contact_email(request.contact_email)
        users = load_users()
        profile = {'full_name': request.full_name.strip(), 'phone': request.phone.strip(),
                   'bio': request.bio.strip(), 'email': contact_email}
        users[email] = {**users.get(email, {}), **profile}
        user = store.update_profile(email, request.full_name, persist_profile=lambda: save_users(users))
    except (LookupError, PermissionError, ValueError) as error:
        raise account_error(error) from error
    except OSError as error:
        raise HTTPException(503, 'Không thể lưu hồ sơ cá nhân. Vui lòng thử lại.') from error
    return {'user': identity_user(user), 'profile': profile}


@app.get('/api/v1/admin/users')
def admin_users(principal: Annotated[dict, Depends(current_principal)]):
    require_roles(principal, 'Admin')
    return {'users': AccountStore().list_users()}


@app.get('/api/v1/admin/users/{email:path}')
def admin_user_detail(email: str, principal: Annotated[dict, Depends(current_principal)]):
    require_roles(principal, 'Admin')
    store = AccountStore()
    user = managed_user(store, email)
    email = user['email']
    profile = load_users().get(email, {})
    public_fields = {'id', 'title', 'type', 'location', 'price', 'area', 'bedrooms',
                     'bathrooms', 'status', 'description', 'contact', 'created_at',
                     'user_id', 'rating', 'reviews', 'images'}
    listings = [{key: value for key, value in listing.items() if key in public_fields}
                for listing in load_properties() if listing.get('user_id') == email]
    posts = load_forum_posts()
    return {
        'user': user,
        'profile': {'full_name': user['full_name'], 'email': str(profile.get('email', email)),
                    'phone': str(profile.get('phone', '')), 'bio': str(profile.get('bio', ''))},
        'activity': {
            'listing_count': len(listings),
            'sale_count': sum(item.get('status') == 'Bán' for item in listings),
            'rent_count': sum(item.get('status') == 'Cho thuê' for item in listings),
            'favorite_count': len(profile.get('favorites', [])),
            'forum_post_count': sum(post.get('author_id') == email for post in posts),
            'forum_comment_count': sum(comment.get('author_id') == email
                                      for post in posts for comment in post.get('comments', [])),
            'support_request_count': sum(message.get('from') == email and message.get('to') == 'support'
                                         for message in load_messages()),
        },
        'listings': listings,
        'audit': store.audit_history(email),
    }


@app.post('/api/v1/admin/users', status_code=201)
def admin_create_user(request: AdminCreateUserRequest,
                      principal: Annotated[dict, Depends(current_principal)]):
    require_roles(principal, 'Admin')
    try:
        user = AccountStore().create_account(request.email, request.full_name,
                                             request.password, 'User', actor=actor_id(principal))
    except ValueError as error:
        raise account_error(error) from error
    return {'user': user}


@app.post('/api/v1/admin/users/{email:path}/update')
def admin_update_user(email: str, request: AdminUpdateUserRequest,
                      principal: Annotated[dict, Depends(current_principal)]):
    require_roles(principal, 'Admin')
    store = AccountStore()
    user = managed_user(store, email)
    if not request.full_name.strip():
        raise HTTPException(422, 'Họ và tên không được để trống.')
    users = load_users()
    users[user['email']] = {
        **users.get(user['email'], {}), 'email': users.get(user['email'], {}).get('email', user['email']),
        'full_name': request.full_name.strip(), 'phone': request.phone.strip(),
        'bio': request.bio.strip(),
    }
    try:
        user = store.update_user(user['email'], request.full_name, request.admin_note,
                                 actor=actor_id(principal), changed_fields=('phone', 'bio'),
                                 persist_profile=lambda: save_users(users))
    except (LookupError, PermissionError, ValueError) as error:
        raise account_error(error) from error
    except OSError as error:
        raise HTTPException(503, 'Không thể lưu hồ sơ người dùng. Vui lòng thử lại.') from error
    return {'user': user}


@app.post('/api/v1/admin/users/{email:path}/status')
def admin_user_status(email: str, request: AdminStatusRequest,
                      principal: Annotated[dict, Depends(current_principal)]):
    require_roles(principal, 'Admin')
    try:
        user = AccountStore().set_user_status(email, request.is_active, request.reason,
                                              actor=actor_id(principal))
    except (LookupError, PermissionError, ValueError) as error:
        raise account_error(error) from error
    return {'user': user}


@app.post('/api/v1/admin/users/{email:path}/password')
def admin_user_password(email: str, request: AdminPasswordRequest,
                        principal: Annotated[dict, Depends(current_principal)]):
    require_roles(principal, 'Admin')
    try:
        user = AccountStore().reset_user_password(email, request.password, request.reason,
                                                  actor=actor_id(principal))
    except (LookupError, PermissionError, ValueError) as error:
        raise account_error(error) from error
    return {'user': user}


@app.get('/api/v1/model')
def model_info(principal: Annotated[dict, Depends(current_principal)]):
    require_roles(principal, 'Manager')
    return {
        'name': 'Comparable listing median',
        'version': 'comparable-median-v1',
        'type': 'deterministic-estimator',
        'ready': True,
        'features_used': ['location', 'property_type', 'area'],
        'limitations': [
            'Uses asking prices from the HomeNest catalog, not confirmed transactions.',
            'Additional property attributes are not used to adjust the estimate.',
            'The sample range is not a calibrated confidence interval.',
        ],
    }


@app.post('/api/v1/valuations')
def valuation(subject: dict[str, Any],
              principal: Annotated[dict, Depends(current_principal)]):
    require_roles(principal, 'User', 'Manager')
    if errors := validate_subject(subject):
        raise HTTPException(422, detail=errors)
    result = estimate(load_properties(), subject['location'], subject['type'], float(subject['area']))
    return {**result, 'method': 'comparable-median-v1', 'source': 'HomeNest listing catalog',
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'status': 'ok' if result['samples'] else 'insufficient_data'}


@app.post('/api/v1/valuations/batch')
def batch(request: BatchRequest,
          principal: Annotated[dict, Depends(current_principal)]):
    require_roles(principal, 'Manager')
    return {'results': estimate_batch(load_properties(), request.subjects),
            'generated_at': datetime.now(timezone.utc).isoformat()}
