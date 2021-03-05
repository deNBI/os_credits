import functools
from os_credits.settings import config
from aiohttp.web_exceptions import HTTPForbidden, HTTPUnauthorized


def auth_required(func):
    @functools.wraps(func)
    async def auth_wrapper(request):
        if 'X-API-KEY' not in request.headers:
            raise HTTPUnauthorized(text='No API Key provided')
        key = request.headers['X-API-KEY']
        if key != config["API_KEY"] or not config["API_KEY"]:
            raise HTTPForbidden(text='Wrong API Key')

        return await func(request)

    return auth_wrapper
