from contextvars import ContextVar
from typing import Any, Dict, Optional

from aiohttp import BasicAuth, ClientSession

from os_credits.exceptions import (
    AttributeNotExistsError,
    ConsistencyError,
    GroupNotExistsError,
    InternalError,
    RequestError,
)
from os_credits.log import requests_logger
from os_credits.settings import config

# will be instantiated/set it in the context of the aiohttp.web.application on
# startup to have its lifetime bound to the application
client_session: ContextVar[ClientSession] = ContextVar("client_session")


async def perun_set(url: str, params: Optional[Dict[str, Any]] = None) -> None:
    await _perun_rpc(url, params)


async def perun_get(url: str, params: Optional[Dict[str, Any]] = None) -> Any:
    return await _perun_rpc(url, params)


async def _perun_rpc(url: str, params: Optional[Dict[str, Any]] = None) -> Any:
    request_url = f"{config['perun']['rpc_base_url']}/{url}"
    requests_logger.debug(
        "Sending POST request `%s` with data `%s`", request_url, params
    )
    try:
        _client = client_session.get()
    except LookupError:
        # in case of not running inside the application, i.e. inside an ipython console for
        # testing purposes
        # not nice to create a new session per request but ok for testing
        _client = ClientSession(
            auth=BasicAuth(
                config["service_user"]["login"], config["service_user"]["password"]
            )
        )

    async with _client.post(request_url, json=params) as response:
        response_content = await response.json()
        requests_logger.debug(
            "Received response %r with content %r", response, response_content
        )
        if response_content and "errorId" in response_content:
            # Some kind of error has occured
            if response_content["name"] == "GroupNotExistsException":
                raise GroupNotExistsError(response_content["message"])
            elif response_content["name"] == "AttributeNotExistsException":
                raise AttributeNotExistsError(response_content["message"])
            elif response_content["name"] == "InternalErrorException":
                raise InternalError(response_content["message"])
            elif response_content["name"] == "ConsistencyErrorException":
                raise ConsistencyError(response_content["message"])
            else:
                raise RequestError(response_content["message"])

        return response_content
