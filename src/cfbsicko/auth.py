"""Supabase JWT verification (same shape as cfbfPy, no warehouse coupling)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import jwt
import requests
from fastapi import Depends, Header, HTTPException

from cfbsicko.config import Config

logger = logging.getLogger(__name__)

_DEFAULT_JWT_LEEWAY_SECONDS = 120
_JWKS_ALLOWED_ALGORITHMS = ("RS256", "ES256")


class TokenVerificationError(ValueError):
    """Raised when a bearer token cannot be verified."""


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: str
    email: str | None
    role: str
    email_confirmed: bool
    claims: dict[str, Any]


class SupabaseTokenVerifier:
    def __init__(
        self,
        *,
        supabase_url: str = "",
        anon_key: str = "",
        jwt_secret: str = "",
        jwks_url: str = "",
        audience: str = "authenticated",
        issuer: str | None = None,
        fetch_user_on_verify: bool = True,
        timeout_seconds: float = 5.0,
        leeway_seconds: int = _DEFAULT_JWT_LEEWAY_SECONDS,
    ):
        self.supabase_url = supabase_url.rstrip("/")
        self.anon_key = anon_key
        self.jwt_secret = jwt_secret
        self.audience = audience
        self.issuer = issuer or (f"{self.supabase_url}/auth/v1" if self.supabase_url else None)
        self.fetch_user_on_verify = fetch_user_on_verify
        self.timeout_seconds = timeout_seconds
        self.leeway_seconds = max(0, int(leeway_seconds))
        self.userinfo_url = f"{self.supabase_url}/auth/v1/user" if self.supabase_url else None
        resolved = jwks_url or (
            f"{self.supabase_url}/auth/v1/.well-known/jwks.json" if self.supabase_url else ""
        )
        self._jwks_client = jwt.PyJWKClient(resolved) if resolved and not self.jwt_secret else None

    def verify_access_token(self, token: str) -> AuthenticatedUser:
        if not token:
            raise TokenVerificationError("Missing bearer token")
        claims = self._decode_claims(token)
        if claims.get("role") != "authenticated":
            raise TokenVerificationError("Authenticated user token required")
        user_id = str(claims.get("sub") or "").strip()
        if not user_id:
            raise TokenVerificationError("Token missing subject claim")
        email_confirmed = self._email_confirmed_from_claims(claims)
        if email_confirmed is not True and self.fetch_user_on_verify:
            live = self._fetch_email_confirmation(token)
            if live is not None:
                email_confirmed = live
        if email_confirmed is None:
            email_confirmed = False
        return AuthenticatedUser(
            user_id=user_id,
            email=claims.get("email"),
            role=str(claims.get("role", "")),
            email_confirmed=bool(email_confirmed),
            claims=claims,
        )

    def _decode_claims(self, token: str) -> dict[str, Any]:
        decode_kwargs: dict[str, Any] = {
            "algorithms": ["HS256"] if self.jwt_secret else None,
            "audience": self.audience,
            "leeway": self.leeway_seconds,
            "options": {"require": ["exp", "iat", "sub"]},
        }
        if self.issuer:
            decode_kwargs["issuer"] = self.issuer
        try:
            if self.jwt_secret:
                return jwt.decode(token, self.jwt_secret, **decode_kwargs)
            if self._jwks_client is None:
                raise TokenVerificationError("Supabase verifier is not configured")
            header = jwt.get_unverified_header(token)
            algorithm = str(header.get("alg") or "")
            if algorithm not in _JWKS_ALLOWED_ALGORITHMS:
                raise TokenVerificationError(f"Disallowed JWT algorithm: {algorithm or 'missing'}")
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            decode_kwargs["algorithms"] = list(_JWKS_ALLOWED_ALGORITHMS)
            return jwt.decode(token, signing_key.key, **decode_kwargs)
        except TokenVerificationError:
            raise
        except jwt.PyJWTError as exc:
            raise TokenVerificationError(f"Invalid bearer token: {exc}") from exc

    def _email_confirmed_from_claims(self, claims: dict[str, Any]) -> bool | None:
        if claims.get("email_verified") is True:
            return True
        meta = claims.get("user_metadata")
        if isinstance(meta, dict) and meta.get("email_verified") is True:
            return True
        if claims.get("email_confirmed_at"):
            return True
        return None

    def _fetch_email_confirmation(self, token: str) -> bool | None:
        if not self.userinfo_url:
            return None
        headers = {"Authorization": f"Bearer {token}"}
        if self.anon_key:
            headers["apikey"] = self.anon_key
        try:
            response = requests.get(self.userinfo_url, headers=headers, timeout=self.timeout_seconds)
        except requests.RequestException as exc:
            logger.warning("Supabase userinfo request failed: %s", exc)
            return None
        if response.status_code != 200:
            return None
        payload = response.json()
        return bool(payload.get("email_confirmed_at") or payload.get("confirmed_at"))


def build_token_verifier() -> SupabaseTokenVerifier | None:
    if not Config.WEB_AUTH_ENABLED:
        return None
    if not Config.SUPABASE_URL and not Config.SUPABASE_JWT_SECRET:
        return None
    return SupabaseTokenVerifier(
        supabase_url=Config.SUPABASE_URL,
        anon_key=Config.supabase_browser_key(),
        jwt_secret=Config.SUPABASE_JWT_SECRET,
        jwks_url=Config.SUPABASE_JWKS_URL,
        audience=Config.SUPABASE_JWT_AUDIENCE,
        fetch_user_on_verify=Config.SUPABASE_FETCH_USER_ON_VERIFY,
    )


def bearer_token(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    return authorization.split(" ", 1)[1].strip()


def get_verifier() -> SupabaseTokenVerifier:
    verifier = build_token_verifier()
    if verifier is None:
        raise HTTPException(status_code=503, detail="Auth is not configured")
    return verifier


def get_auth_user(
    token: str = Depends(bearer_token),
    verifier: SupabaseTokenVerifier = Depends(get_verifier),
) -> AuthenticatedUser:
    try:
        user = verifier.verify_access_token(token)
    except TokenVerificationError as exc:
        raise HTTPException(status_code=401, detail="Invalid bearer token") from exc
    if Config.REQUIRE_EMAIL_CONFIRMED and not user.email_confirmed:
        raise HTTPException(status_code=403, detail="Email is not confirmed")
    return user
