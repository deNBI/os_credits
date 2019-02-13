"""
Implements the RPC-Calls of the AttributesManager
https://perun-aai.org/documentation/technical-documentation/rpc-api/rpc-javadoc-AttributesManager.html
"""

from __future__ import annotations

from logging import getLogger
from typing import Any, Dict, List, Optional, cast

from .attributes import PerunAttribute
from .requests import perun_get, perun_set

_logger = getLogger(__name__)
_URL = "attributesManager"


async def get_attributes(
    group_id: int, attr_names: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {"group": group_id}
    if attr_names:
        params.update({"attrNames": attr_names})
    # cast is only for type checking purposes
    return cast(
        List[Dict[str, Any]], await perun_get(f"{_URL}/getAttributes", params=params)
    )


async def set_attribute(group_id: int, attribute: PerunAttribute[Any]) -> None:
    await perun_set(
        f"{_URL}/setAttribute",
        {"group": group_id, "attribute": attribute.to_perun_dict()},
    )


async def set_attributes(group_id: int, attributes: List[PerunAttribute[Any]]) -> None:
    await perun_set(
        f"{_URL}/setAttributes",
        {
            "group": group_id,
            "attributes": [attr.to_perun_dict() for attr in attributes],
        },
    )
