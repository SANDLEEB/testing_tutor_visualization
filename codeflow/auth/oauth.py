from authlib.integrations.starlette_client import OAuth

import config

oauth = OAuth()

if config.GOOGLE_SSO_ENABLED:
    oauth.register(
        name='google',
        client_id=config.GOOGLE_CLIENT_ID,
        client_secret=config.GOOGLE_CLIENT_SECRET,
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid email profile'},
    )

if config.MICROSOFT_SSO_ENABLED:
    oauth.register(
        name='microsoft',
        client_id=config.MICROSOFT_CLIENT_ID,
        client_secret=config.MICROSOFT_CLIENT_SECRET,
        server_metadata_url=(
            f'https://login.microsoftonline.com/{config.MICROSOFT_TENANT_ID}'
            '/v2.0/.well-known/openid-configuration'
        ),
        client_kwargs={'scope': 'openid email profile'},
    )
