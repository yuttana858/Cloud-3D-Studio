"""Small metadata-only ASGI gateway. Modal's proxy authenticates before this app runs."""

import json
from collections.abc import Mapping

from cloud_3d_studio import __version__


def connection_application(settings: Mapping[str, str]):
    """No tokens, CAD libraries, customer files, or job-submission routes belong here."""
    identity = {
        "service": "cloud-3d-studio",
        "protocol": 1,
        "app": "cloud-3d-studio",
        "workspace": settings.get("CLOUD3D_WORKSPACE", ""),
        "environment": settings.get("CLOUD3D_ENVIRONMENT", ""),
        "version": __version__,
        "conversionReady": False,
    }

    async def application(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                event = await receive()
                if event["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif event["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
            return
        if scope["path"] != "/connection":
            status, payload = 404, {"error": "Not found"}
        elif scope["method"] != "GET":
            status, payload = 405, {"error": "Method not allowed"}
        elif not identity["workspace"] or not identity["environment"]:
            status, payload = 503, {"error": "Deployment identity is not configured"}
        else:
            status, payload = 200, identity
        body = json.dumps(payload).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    return application
