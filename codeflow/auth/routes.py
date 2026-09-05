from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from nicegui import app as nicegui_app

import config
from auth.db import get_session
from auth.models import AuthProvider
from auth.oauth import oauth
from auth.service import get_or_create_oauth_user


def _log_in(user) -> None:
    nicegui_app.storage.user.update({
        'user_id': user.id,
        'email': user.email,
        'full_name': user.full_name,
        'role': user.role.value,
    })


def _login_route(provider: str):
    async def login(request: Request):
        redirect_uri = request.url_for('oauth_callback', provider=provider)
        client = oauth.create_client(provider)
        return await client.authorize_redirect(request, str(redirect_uri))

    return login


async def oauth_callback(request: Request, provider: str):
    client = oauth.create_client(provider)
    token = await client.authorize_access_token(request)
    userinfo = token.get('userinfo') or await client.userinfo(token=token)

    email = userinfo['email']
    full_name = userinfo.get('name', email)
    sub = userinfo.get('sub', '')

    with get_session() as session:
        user = get_or_create_oauth_user(
            session, email, full_name, AuthProvider(provider), sub
        )
        if not user.is_active:
            return RedirectResponse('/login?error=inactive')
        _log_in(user)

    return RedirectResponse('/')


def register(app: FastAPI) -> None:
    if config.GOOGLE_SSO_ENABLED:
        app.get('/auth/google/login')(_login_route('google'))
    if config.MICROSOFT_SSO_ENABLED:
        app.get('/auth/microsoft/login')(_login_route('microsoft'))

    app.get('/auth/{provider}/callback', name='oauth_callback')(oauth_callback)
