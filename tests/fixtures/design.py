#!/usr/bin/env python3
# Ending design file

from __future__ import annotations

from ending import *
from ending.ast import *
from ending.cli.design import HTTPDesign


class Design(HTTPDesign):
    async def send(self, payload: str) -> bytes:
        """Sends given payload to the target and returns the response as bytes.
        If a valid value exists, it must be preprended.
        """
        response = await self.session.post(
            "http://target.com/...",
            data={
                {
                    "id": f"1{{payload}}",
                }
            },
        )
        return response.content
