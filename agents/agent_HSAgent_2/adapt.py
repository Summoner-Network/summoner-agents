import httpx
import json
import os
from typing import Optional, Any, Dict, Union

# Define a custom exception for clarity
class APIError(Exception):
    def __init__(self, message, status_code: int, response_text: str):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text

class _BaseClient:
    """
    An internal base class to handle shared state and the httpx request logic.
    Not intended for direct use.
    """
    def __init__(self, base_url: str):
        self._client = httpx.AsyncClient(base_url=base_url)
        self.token: Optional[str] = None
        self.user_id: Optional[str] = None
        self.username: Optional[str] = None
        self.auth_method: Optional[str] = None
        self._last_creds: Optional[Dict[str, str]] = None # Cache credentials for re-login

    async def _request(
        self,
        method: str,
        path: str,
        expected_status: int,
        json_body: Optional[Dict] = None,
        params: Optional[Dict] = None,
        _is_retry: bool = False # Internal flag to prevent infinite loops
    ) -> Any:
        """A private helper that now includes automatic re-authentication."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        try:
            response = await self._client.request(
                method, path, headers=headers,
                json=json_body, params=params, timeout=10.0
            )

            if response.status_code != expected_status:
                # [FIX] This is the core of the fix. We treat authentication requests
                # as "sacred" and do not attempt to re-login if they fail.
                is_auth_request = path.startswith("/api/auth/")

                if response.status_code == 401 and not _is_retry and not is_auth_request:
                    if not self._last_creds:
                        raise APIError("Token expired and no credentials available for re-login.", 401, response.text)
                    
                    await self.login(self._last_creds)
                    
                    return await self._request(method, path, expected_status, json_body, params, _is_retry=True)

                if is_auth_request and response.status_code == 401:
                    raise APIError("Invalid credentials", 401, response.text)
                
                raise APIError(
                    f"API Error: Expected status {expected_status} but got {response.status_code}",
                    response.status_code, response.text
                )

            return None if response.status_code == 204 else response.json()
        except httpx.RequestError as e:
            raise APIError(f"HTTP request failed: {e}", 0, "") from e

    def _check_auth(self, method_name: str):
        if not self.token or not self.user_id or not self.username:
            raise RuntimeError(f"Must be logged in to call {method_name}. Please call login() first.")

    async def close(self):
        await self._client.aclose()
        
    async def login(self, creds: Dict[str, str]):
        raise NotImplementedError

class SummonerAuthAPIClient:
    """Handles authentication and Principal Secret (API Key) endpoints."""
    def __init__(self, parent_client: '_BaseClient'):
        self._client = parent_client

    async def login(self, creds: Dict[str, str]) -> None:
        self._client._last_creds = creds
        
        key = creds.get("key")
        username = creds.get("username")
        password = creds.get("password")

        login_payload = {}
        if key:
            login_payload = {"key": key}
            self._client.auth_method = 'key'
        elif username and password:
            login_payload = creds
            self._client.auth_method = 'password'
        else:
            raise ValueError("Credentials must include either 'key' or both 'username' and 'password'")

        try:
            login_res = await self._client._request("POST", "/api/auth/login", 200, json_body=login_payload)
        except APIError as e:
            if e.status_code == 401 and self._client.auth_method == 'password':
                await self._client._request("POST", "/api/auth/register", 201, json_body=creds)
                login_res = await self._client._request("POST", "/api/auth/login", 200, json_body=creds)
            else:
                raise e

        self._client.token = login_res.get("jwt")
        if not self._client.token:
            raise APIError("Login succeeded but did not return a JWT token", 200, json.dumps(login_res))

        me_res = await self._client._request("GET", "/api/account/me", 200)
        account_data = me_res.get("account", {})
        self._client.user_id = account_data.get("id")
        self._client.username = account_data.get("attrs", {}).get("username")

        if not self._client.user_id or not self._client.username:
            raise APIError("Authenticated but failed to retrieve user details from /me", 200, json.dumps(me_res))

    async def associate_secret(self, secret: str) -> Dict:
        self._client._check_auth("associate_secret")
        if self._client.auth_method != 'password':
            raise PermissionError("Cannot provision new secrets when authenticated with a primary user session.")
        return await self._client._request("POST", "/api/agent/associate", 200, json_body={"secret": secret})

    async def revoke_secret(self, secret: str) -> Dict:
        self._client._check_auth("revoke_secret")
        if self._client.auth_method != 'password':
            raise PermissionError("Cannot revoke secrets when authenticated with a primary user session.")
        return await self._client._request("POST", "/api/agent/revoke", 200, json_body={"secret": secret})

    async def check_secret(self, account_id: Union[str, int], secret: str) -> Dict:
        path = f"/api/agent/check?account={account_id}"
        return await self._client._request("POST", path, 200, json_body={"secret": secret})

    async def verify_key(self, key: str) -> Dict:
        path = "/api/agent/verify_key"
        return await self._client._request("POST", path, 200, json_body={"key": key})

class SummonerBossAPIClient:
    """Handles BOSS (Objects & Associations) endpoints."""
    def __init__(self, parent_client: '_BaseClient'):
        self._client = parent_client

    async def get_object(self, otype: int, obj_id: Union[str, int]) -> Dict:
        self._client._check_auth("get_object")
        path = f"/api/objects/{self._client.user_id}/{otype}/{obj_id}"
        return await self._client._request("GET", path, 200)

    async def put_object(self, obj: Dict) -> Dict:
        self._client._check_auth("put_object")
        path = f"/api/objects/{self._client.user_id}"
        return await self._client._request("PUT", path, 201, json_body=obj)
        
    async def remove_object(self, otype: int, obj_id: Union[str, int]) -> Dict:
        self._client._check_auth("remove_object")
        path = f"/api/objects/{self._client.user_id}/{otype}/{obj_id}"
        return await self._client._request("DELETE", path, 200)

    async def get_associations(self, type: str, source_id: Union[str, int], params: Optional[Dict] = None) -> Dict:
        self._client._check_auth("get_associations")
        path = f"/api/objects/{self._client.user_id}/associations/{type}/{source_id}"
        return await self._client._request("GET", path, 200, params=params)

    async def put_association(self, association: Dict) -> Dict:
        self._client._check_auth("put_association")
        path = f"/api/objects/{self._client.user_id}/associations"
        return await self._client._request("PUT", path, 201, json_body=association)

    async def remove_association(self, type: str, source_id: Union[str, int], target_id: Union[str, int]) -> Dict:
        self._client._check_auth("remove_association")
        path = f"/api/objects/{self._client.user_id}/associations/{type}/{source_id}/{target_id}"
        return await self._client._request("DELETE", path, 200)

class SummonerChainsAPIClient:
    """Handles Fathom (Chains) endpoints."""
    def __init__(self, parent_client: '_BaseClient'):
        self._client = parent_client
        
    async def append(self, chain_key: Dict, data: Dict) -> Dict:
        self._client._check_auth("append")
        path = f"/api/chains/append/{self._client.username}/{chain_key['chainName']}/{chain_key['shardId']}"
        return await self._client._request("POST", path, 201, json_body=data)
        
    async def get_metadata(self, chain_key: Dict) -> Dict:
        self._client._check_auth("get_metadata")
        path = f"/api/chains/metadata/{self._client.username}/{chain_key['chainName']}/{chain_key['shardId']}"
        return await self._client._request("GET", path, 200)

    async def delete(self, chain_key: Dict) -> Dict:
        """
        Permanently deletes an entire Fathom chain and all of its blocks.
        """
        self._client._check_auth("delete")
        path = f"/api/chains/{self._client.username}/{chain_key['chainName']}/{chain_key['shardId']}"
        return await self._client._request("DELETE", path, 200)

class SummonerAPIClient(_BaseClient):
    """
    The main high-level client, composing specialized sub-clients.
    """
    def __init__(self, base_url: str):
        if not base_url:
            raise ValueError("base_url is required")
        super().__init__(base_url)
        self.auth = SummonerAuthAPIClient(self)
        self.boss = SummonerBossAPIClient(self)
        self.chains = SummonerChainsAPIClient(self)

    async def login(self, creds: Dict[str, str]):
        """
        Authenticates the client via the auth sub-client.
        This populates the session state for all other sub-clients.
        """
        await self.auth.login(creds)
    
    async def narrow(self) -> Optional[str]:
        """
        Performs session narrowing. If authenticated with a primary credential
        (password), this method provisions a new, single-use API key and
        re-authenticates the client with it.
        """
        self._check_auth("narrow")

        if self.auth_method == 'key':
            return None
        
        if self.auth_method != 'password':
            raise RuntimeError(f"Cannot narrow session from an unknown or unsupported auth method: {self.auth_method}")

        new_secret_bytes = os.urandom(32)
        new_secret_hex = f"0x{new_secret_bytes.hex()}"
        
        assoc_res = await self.auth.associate_secret(new_secret_hex)
        confirmed_secret = assoc_res.get("secret")
        if not confirmed_secret:
            raise APIError("Failed to associate new secret: server did not confirm the secret.", 500, json.dumps(assoc_res))

        api_key = f"{self.username}%{confirmed_secret}"
        
        await self.login({"key": api_key})
        
        return api_key

    async def close(self):
        """Closes the underlying httpx client session."""
        await super().close()

