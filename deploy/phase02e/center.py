"""Isolated HTTPS acceptance center; never use as production application entrypoint.

MHM_P2E_CONTROL points to a private local file containing an empty string, 503,
or timeout. Fault injection affects only the isolated agent routes.
"""
import asyncio
import os
from pathlib import Path

from starlette.responses import JSONResponse

from app.main import create_app


def create_center():
    application = create_app()
    control = Path(os.environ['MHM_P2E_CONTROL'])

    class ControlledAgentFaults:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope['type'] == 'http' and scope['path'].startswith('/api/v1/agent/'):
                mode = control.read_text().strip() if control.exists() else ''
                if mode == 'timeout':
                    await asyncio.sleep(20)
                if mode in {'timeout', '503'}:
                    return await JSONResponse({'detail':'isolated acceptance fault'},status_code=503,
                                              headers={'Retry-After':'2'})(scope,receive,send)
            await self.app(scope,receive,send)

    application.add_middleware(ControlledAgentFaults)
    return application
