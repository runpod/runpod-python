"""account authentication requests and api key verification."""

from runpod.api.graphql import run_graphql_query_async
from runpod.api.queries.user import QUERY_USER
from runpod.error import AuthenticationError, QueryError
from runpod.user_agent import APPS_USER_AGENT


class AuthApiClient:
    """browser approval and credential verification transport."""

    async def create_auth_request(self) -> dict:
        response = await run_graphql_query_async(
            """mutation createFlashAuthRequest {
                createFlashAuthRequest { id status expiresAt }
            }""",
            anonymous=True,
            headers={"User-Agent": APPS_USER_AGENT},
        )
        return response["data"].get("createFlashAuthRequest") or {}

    async def get_auth_request_status(self, request_id: str) -> dict:
        response = await run_graphql_query_async(
            """query flashAuthRequestStatus($flashAuthRequestId: String!) {
                flashAuthRequestStatus(flashAuthRequestId: $flashAuthRequestId) {
                    id status
                }
            }""",
            variables={"flashAuthRequestId": request_id},
            anonymous=True,
            headers={"User-Agent": APPS_USER_AGENT},
        )
        return response["data"].get("flashAuthRequestStatus") or {}

    async def verify_api_key(self, api_key: str) -> bool:
        try:
            response = await run_graphql_query_async(
                QUERY_USER, api_key=api_key, headers={"User-Agent": APPS_USER_AGENT}
            )
        except (AuthenticationError, QueryError):
            return False
        myself = (response.get("data") or {}).get("myself") or {}
        return bool(myself.get("id"))
