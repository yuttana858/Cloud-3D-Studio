"""Small authenticated dispatch ASGI gateway. Modal's proxy authenticates before this app runs."""

import json
from collections.abc import Mapping

from cloud_3d_studio import __version__


def connection_application(settings: Mapping[str, str], dispatch=None, cancel=None):
    """Only bounded job references cross this gateway; CAD bytes use the owned transfer API."""
    identity = {
        "service": "cloud-3d-studio",
        "protocol": 1,
        "app": "cloud-3d-studio",
        "workspace": settings.get("CLOUD3D_WORKSPACE", ""),
        "environment": settings.get("CLOUD3D_ENVIRONMENT", ""),
        "version": __version__,
        "conversionReady": settings.get("CLOUD3D_NATIVE_VERIFIED") == "1" and dispatch is not None,
        "jobApiVersion": 1,
        "diagnosticApiVersion": 1,
        "appearanceApiVersion": 1,
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
        if scope["path"] in ("/jobs", "/cancel") and scope["method"] == "POST":
            if not identity["conversionReady"]:
                status, payload = 503, {"error": "Native worker is not verified"}
            else:
                try:
                    content = bytearray()
                    while True:
                        event = await receive()
                        if event["type"] != "http.request":
                            raise ValueError()
                        content.extend(event.get("body", b""))
                        if len(content) > 4096:
                            raise ValueError()
                        if not event.get("more_body", False):
                            break
                    request = json.loads(content)
                    if scope["path"] == "/jobs":
                        from .remote import JobReference

                        JobReference.parse(request)
                        payload = {"accepted": True, "callId": await dispatch(request)}
                    else:
                        import re

                        if (
                            not isinstance(request, dict)
                            or set(request) != {"callId"}
                            or not re.fullmatch(r"fc-[A-Za-z0-9]{8,128}", request["callId"])
                        ):
                            raise ValueError()
                        if cancel is None:
                            raise ValueError()
                        await cancel(request["callId"])
                        payload = {"requested": True}
                    status = 202
                except Exception:
                    # The body contains an expiring capability; never log/echo it.
                    status, payload = 400, {"error": "Invalid or unavailable job request"}
        elif scope["path"] != "/connection":
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
