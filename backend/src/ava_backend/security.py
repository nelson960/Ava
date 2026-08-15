from __future__ import annotations

import hashlib
import hmac


def safety_identifier(client_instance_id: str, secret: str) -> str:
    """Return a stable, privacy-preserving identifier accepted by OpenAI."""

    return hmac.new(
        secret.encode("utf-8"),
        client_instance_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
