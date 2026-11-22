# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins <ed@andexor.net>

"""Verifies a Cloudflare Access JSON Web Token (JWT).

The signature (RS256) is checked with the Web Crypto API that the Workers runtime provides, so no
native Python cryptography package is needed. The claims check is a pure function, so it can be
unit tested without a Worker.
"""

import base64
import json


class AccessError(Exception):
    """The token is missing, malformed, wrongly signed, or fails a claim check. The message is for the log only."""


def b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def check_claims(claims: dict, team_domain: str, aud: str, now: float) -> None:
    """Raise AccessError unless the issuer, audience, and expiry are right."""
    if claims.get("iss") != f"https://{team_domain}":
        raise AccessError("wrong issuer")
    audiences = claims.get("aud")
    if isinstance(audiences, str):
        audiences = [audiences]
    if not isinstance(audiences, list) or aud not in audiences:
        raise AccessError("wrong audience")
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)) or exp <= now:
        raise AccessError("expired")
    nbf = claims.get("nbf")
    if isinstance(nbf, (int, float)) and nbf > now:
        raise AccessError("not valid yet")


# Signing keys by key id, kept for the life of the isolate. Access rotates keys every six weeks and
# publishes the new one beforehand, so an unknown key id means "fetch the list again".
_keys: dict = {}


async def _signing_key(team_domain: str, kid: str):
    if kid not in _keys:
        from js import Object, crypto, fetch
        from pyodide.ffi import to_js

        response = await fetch(f"https://{team_domain}/cdn-cgi/access/certs")
        if not response.ok:
            raise AccessError(f"could not fetch the signing keys: status {response.status}")
        jwks = json.loads(await response.text())
        _keys.clear()
        for jwk in jwks["keys"]:
            _keys[jwk["kid"]] = await crypto.subtle.importKey(
                "jwk",
                to_js(jwk, dict_converter=Object.fromEntries),
                to_js({"name": "RSASSA-PKCS1-v1_5", "hash": "SHA-256"}, dict_converter=Object.fromEntries),
                False,
                to_js(["verify"]),
            )
    if kid not in _keys:
        raise AccessError("unknown signing key")
    return _keys[kid]


async def verify(token: str | None, team_domain: str, aud: str, now: float) -> dict:
    """Return the token's claims, or raise AccessError."""
    if not token:
        raise AccessError("no token")
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
        header = json.loads(b64url_decode(header_b64))
        claims = json.loads(b64url_decode(payload_b64))
        signature = b64url_decode(signature_b64)
    except Exception as e:
        raise AccessError(f"malformed token: {type(e).__name__}") from e
    if header.get("alg") != "RS256":
        raise AccessError("wrong algorithm")

    from js import crypto
    from pyodide.ffi import to_js

    key = await _signing_key(team_domain, header.get("kid"))
    signed = f"{header_b64}.{payload_b64}".encode()
    if not await crypto.subtle.verify("RSASSA-PKCS1-v1_5", key, to_js(signature), to_js(signed)):
        raise AccessError("bad signature")

    check_claims(claims, team_domain, aud, now)
    return claims
