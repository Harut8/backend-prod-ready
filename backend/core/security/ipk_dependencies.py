"""
Custom IPK Dependency Wrappers.

These dependencies wrap identity-plan-kit functionality but allow domain
exceptions to bubble up to our custom exception handlers, providing
consistent error responses across the API.

IPK's built-in dependencies convert exceptions to HTTPException with string
messages, which loses structured error information. These wrappers preserve
the original domain exceptions.
"""

from typing import Annotated

from fastapi import Cookie, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from identity_plan_kit import IdentityPlanKit
from identity_plan_kit.auth.domain.entities import User


# Reusable bearer scheme (optional, won't raise on missing token)
_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_bearer_scheme),
    ] = None,
    access_token: Annotated[str | None, Cookie(alias="access_token")] = None,
) -> User:
    """
    Get the current authenticated user.

    Unlike IPK's built-in dependency, this lets domain exceptions bubble up
    to our custom exception handlers for consistent error responses.

    Checks for JWT token in:
    1. Authorization header (Bearer token)
    2. access_token cookie

    Raises:
        TokenExpiredError: If token has expired
        TokenInvalidError: If token is invalid
        UserNotFoundError: If user doesn't exist
        UserInactiveError: If user account is deactivated
        AuthError: For other auth failures
    """
    from identity_plan_kit.auth.domain.exceptions import AuthError

    # Get token from header or cookie
    token: str | None = None
    if credentials:
        token = credentials.credentials
    elif access_token:
        token = access_token

    if not token:
        raise AuthError(message="Not authenticated", code="NOT_AUTHENTICATED")

    # Get auth service from app state
    kit: IdentityPlanKit = request.app.state.identity_plan_kit
    auth_service = kit.auth_service

    # Let exceptions bubble up naturally - our handlers will catch them
    return await auth_service.get_user_from_token(token)


async def get_optional_user(
    request: Request,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_bearer_scheme),
    ] = None,
    access_token: Annotated[str | None, Cookie(alias="access_token")] = None,
) -> User | None:
    """
    Get the current user if authenticated, None otherwise.

    Useful for endpoints that work for both authenticated and anonymous users.
    """
    from identity_plan_kit.auth.domain.exceptions import AuthError

    # Get token from header or cookie
    token: str | None = None
    if credentials:
        token = credentials.credentials
    elif access_token:
        token = access_token

    if not token:
        return None

    try:
        kit: IdentityPlanKit = request.app.state.identity_plan_kit
        auth_service = kit.auth_service
        return await auth_service.get_user_from_token(token)
    except AuthError:
        return None


def requires_feature(feature_code: str, consume: int = 0):
    """
    Dependency that requires access to a feature and optionally consumes quota.

    Unlike IPK's built-in dependency, this lets domain exceptions bubble up
    to our custom exception handlers for consistent error responses.

    Args:
        feature_code: Required feature code
        consume: Amount of quota to consume (0 = just check access)

    Raises:
        UserPlanNotFoundError: If user has no active plan
        PlanExpiredError: If user's plan has expired
        FeatureNotAvailableError: If feature not in user's plan
        QuotaExceededError: If quota is exceeded
    """
    from identity_plan_kit.plans.domain.exceptions import FeatureNotAvailableError

    async def dependency(
        request: Request,
        user: Annotated[User, Depends(get_current_user)],
    ) -> None:
        kit: IdentityPlanKit = request.app.state.identity_plan_kit
        plan_service = kit.plan_service

        # Let exceptions bubble up naturally - our handlers will catch them
        if consume > 0:
            await plan_service.check_and_consume_quota(
                user_id=user.id,
                feature_code=feature_code,
                amount=consume,
            )
        else:
            has_access = await plan_service.check_feature_access(
                user_id=user.id,
                feature_code=feature_code,
            )
            if not has_access:
                # Get user's plan code for better error message
                user_plan = await plan_service.get_user_plan_or_none(user.id)
                plan_code = user_plan.plan_code if user_plan else None
                raise FeatureNotAvailableError(feature_code, plan_code=plan_code)

    return Depends(dependency)


def requires_plan(plan_codes: list[str] | str):
    """
    Dependency that requires user to have one of the specified plans.

    Args:
        plan_codes: Required plan code(s)

    Raises:
        UserPlanNotFoundError: If user has no active plan
        PlanExpiredError: If user's plan has expired
        FeatureNotAvailableError: If user's plan is not in allowed list
    """
    from identity_plan_kit.plans.domain.exceptions import FeatureNotAvailableError

    if isinstance(plan_codes, str):
        plan_codes = [plan_codes]

    async def dependency(
        request: Request,
        user: Annotated[User, Depends(get_current_user)],
    ) -> None:
        kit: IdentityPlanKit = request.app.state.identity_plan_kit
        plan_service = kit.plan_service

        # This will raise UserPlanNotFoundError or PlanExpiredError
        user_plan = await plan_service.get_user_plan(user.id)

        if user_plan.plan_code not in plan_codes:
            raise FeatureNotAvailableError(
                feature_code=f"plan:{','.join(plan_codes)}",
                plan_code=user_plan.plan_code,
            )

    return Depends(dependency)


# Type aliases for cleaner function signatures
CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalUser = Annotated[User | None, Depends(get_optional_user)]
