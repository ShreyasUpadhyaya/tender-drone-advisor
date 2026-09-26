"""Authentication boundary and safe local-demo identity.

This module intentionally does not decode or trust ad-hoc JWTs. Production must
wire an OIDC verifier that validates issuer, audience, expiry and signature.
The protocol keeps route authorization testable while the local case-study demo
remains credentials-free only when explicitly configured outside production.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from fastapi import Depends, Header, HTTPException, Request, status

from app.settings import Settings, get_settings


class Role(StrEnum):
    ADMIN = "admin"
    REVIEWER = "reviewer"
    VIEWER = "viewer"


@dataclass(frozen=True)
class Actor:
    subject: str
    workspace_id: str
    roles: frozenset[Role]
    demo: bool = False


class AuthenticationUnavailable(Exception):
    pass


class JwtVerifier:
    """Production extension point; implementations must validate signed JWTs."""

    def verify(self, token: str) -> Actor:  # pragma: no cover - integration boundary
        raise AuthenticationUnavailable("OIDC/JWT verification is not configured.")


def get_verifier(_: Settings = Depends(get_settings)) -> JwtVerifier:
    return JwtVerifier()


def _failure(code: str, detail: str, code_status: int) -> HTTPException:
    return HTTPException(status_code=code_status, detail={"error": code, "detail": detail})


def get_actor(
    request: Request,
    authorization: str | None = Header(default=None),
    workspace_id: str | None = Header(default=None, alias="X-Workspace-ID"),
    settings: Settings = Depends(get_settings),
    verifier: JwtVerifier = Depends(get_verifier),
) -> Actor:
    if settings.local_demo_enabled:
        # A visible UI banner documents that this deliberately broad local role is
        # not an authentication implementation.
        actor = Actor(
            subject="local-demo-operator",
            workspace_id=workspace_id or settings.default_workspace_id,
            roles=frozenset(Role),
            demo=True,
        )
    else:
        if settings.auth_mode.lower() != "oidc":
            raise _failure(
                "auth_not_configured",
                "Production access requires a configured OIDC/JWT verifier.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        if not authorization or not authorization.startswith("Bearer "):
            raise _failure("authentication_required", "A bearer token is required.", 401)
        try:
            actor = verifier.verify(authorization.removeprefix("Bearer ").strip())
        except AuthenticationUnavailable:
            raise _failure(
                "auth_not_configured",
                "Production token verification is not configured.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            ) from None
        if not workspace_id or workspace_id != actor.workspace_id:
            raise _failure("workspace_required", "A token-authorized workspace is required.", 403)
    request.state.actor = actor
    return actor


def require_roles(*roles: Role) -> Callable:
    def dependency(actor: Actor = Depends(get_actor)) -> Actor:
        if not actor.roles.intersection(roles):
            raise _failure("forbidden", "This action requires an authorized role.", 403)
        return actor

    return dependency


def require_authenticated(actor: Actor = Depends(get_actor)) -> Actor:
    return actor
