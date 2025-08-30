import asyncio
from typing import Dict, Any, Optional, Tuple

# We depend entirely on the high-level, battle-tested API client.
# This assumes the summoner library is installed or in the python path.
from adapt import SummonerAPIClient, APIError

# ---[ Placeholder ElmTypes - these should live in a shared contract ]---
# These constants define the "types" of our new objects in the BOSS substrate.
class ElmType:
    AgentIdentity = 7001
    AgentSecretVault = 7002
    HandshakeState = 7003
    NonceMarker = 7004

# =============================================================================
#  The HybridNonceStore: The Notary's Office
# =============================================================================
class HybridNonceStore:
    """
    This is a CQRS-pattern implementation of the nonce store. It uses both
    Fathom (for the command/audit side) and BOSS (for the query/lookup side)
    to achieve perfect auditability and maximum performance.
    """
    def __init__(self, api: SummonerAPIClient, self_id: str, peer_id: str, ttl_seconds: int = 60):
        self.api = api
        self.self_id = self_id
        self.peer_id = peer_id
        self.ttl_seconds = ttl_seconds
        self.journal_chain_key = {
            "tenant": self.api.username,
            "chainName": f"nonce-journal-{self.self_id}-{self.peer_id}", # Using URL-safe separator
            "shardId": 0
        }
        self.index_source_id: Optional[str] = None


    async def _get_index_source_id(self) -> str:
        """Helper to lazily fetch the agent's own identity ID for the index."""
        if self.index_source_id:
            return self.index_source_id
        
        identity_assoc = await self.api.boss.get_associations(
            "owns_agent_identity", self.api.user_id, {"agentId": self.self_id}
        )
        if not identity_assoc or not identity_assoc.get("associations"):
             raise RuntimeError(f"Could not find AgentIdentity for agent {self.self_id}")
        self.index_source_id = identity_assoc["associations"][0]["targetId"]
        return self.index_source_id

    async def exists(self, nonce: str) -> bool:
        """
        The QUERY side. Performs a single, high-speed structural query against
        the BOSS "Index Card" system.
        """
        source_id = await self._get_index_source_id()
        # ✅ FIX: Use a URL-safe separator for the association type.
        assoc_type = f"nonce_seen-{nonce}"
        
        try:
            response = await self.api.boss.get_associations(assoc_type, source_id)
            return response.get("count", 0) > 0
        except APIError as e:
            if e.status_code == 404:
                return False
            raise

    def is_expired(self, ts) -> bool:
        from datetime import datetime, timezone
        return (datetime.now(timezone.utc) - ts.replace(tzinfo=timezone.utc)).total_seconds() > self.ttl_seconds

    async def add(self, nonce: str, ts) -> None:
        """
        The COMMAND side. Performs the "Notarization" act.
        It writes to both the Fathom journal and the BOSS index.
        """
        source_id = await self._get_index_source_id()
        # ✅ FIX: Use a URL-safe separator for the association type.
        assoc_type = f"nonce_seen-{nonce}"

        marker_obj = await self.api.boss.put_object({
            "type": ElmType.NonceMarker, "version": 0, "attrs": {"nonce": nonce}
        })
        marker_id = marker_obj["id"]

        await asyncio.gather(
            self.api.chains.append(self.journal_chain_key, {
                "data": {"event": "NONCE_SEEN", "nonce": nonce, "timestamp_utc": ts.isoformat()}
            }),
            self.api.boss.put_association({
                "type": assoc_type, "sourceId": source_id, "targetId": marker_id,
                "time": str(int(ts.timestamp() * 1000)), "position": str(int(ts.timestamp() * 1000)), "attrs": {}
            })
        )

# =============================================================================
#  The SubstrateStateStore: The Agent's New Brain
# =============================================================================
class SubstrateStateStore:
    """
    This is the new, substrate-native replacement for the entire db_sdk.py.
    It provides a high-level interface for managing the agent's state,
    translating every call into the appropriate BOSS primitives.
    """
    def __init__(self, api: SummonerAPIClient, self_agent_id: str):
        self.api = api
        self.self_agent_id = self_agent_id
        self._identity_id_cache: Optional[str] = None

    async def _get_self_identity_id(self) -> str:
        """Helper to find and cache our own AgentIdentity object ID."""
        if self._identity_id_cache:
            return self._identity_id_cache

        assoc = await self.api.boss.get_associations(
            "owns_agent_identity", self.api.user_id, {"agentId": self.self_agent_id}
        )
        if not assoc or not assoc.get("associations"):
            raise RuntimeError(f"Could not find AgentIdentity for agent {self.self_agent_id}")
        self._identity_id_cache = assoc["associations"][0]["targetId"]
        return self._identity_id_cache

    async def _find_handshake_state_object(self, role: str, peer_id: str) -> Optional[Dict[str, Any]]:
        """Finds the HandshakeState object for a given conversation thread."""
        self_identity_id = await self._get_self_identity_id()
        # ✅ FIX: Use a URL-safe separator for the association type.
        assoc_type = f"handshake_state-{role}-{peer_id}"
        
        associations = await self.api.boss.get_associations(assoc_type, self_identity_id)
        if not associations.get("associations"):
            return None
        
        state_object_id = associations["associations"][0]["targetId"]
        return await self.api.boss.get_object(ElmType.HandshakeState, state_object_id)

    async def find_role_states(self, role: str) -> list[Dict[str, Any]]:
        """Finds all HandshakeState objects for a given role."""
        self_identity_id = await self._get_self_identity_id()
        
        associations = await self.api.boss.get_associations("has_handshake_state", self_identity_id, {"limit": 1000})

        if not associations.get("associations"):
            return []

        state_ids = [assoc["targetId"] for assoc in associations.get("associations", [])]
        tasks = [self.api.boss.get_object(ElmType.HandshakeState, state_id) for state_id in state_ids]
        all_states = await asyncio.gather(*tasks)

        return [state for state in all_states if state and state.get("attrs", {}).get("role") == role]

    async def ensure_role_state(self, role: str, peer_id: str, default_state: str) -> Tuple[Dict[str, Any], bool]:
        """
        Finds or creates the HandshakeState object for a conversation.
        Returns (state_object_dict, created_boolean).
        """
        state_obj = await self._find_handshake_state_object(role, peer_id)
        if state_obj:
            return state_obj, False

        self_identity_id = await self._get_self_identity_id()
        
        new_state_attrs = {
            "selfId": self.self_agent_id, "peerId": peer_id, "role": role, "state": default_state,
            "exchange_count": 0, "finalize_retry_count": 0, "local_nonce": None,
            "peer_nonce": None, "local_reference": None, "peer_reference": None, "peer_address": None
        }
        create_res = await self.api.boss.put_object({
            "type": ElmType.HandshakeState, "version": 0, "attrs": new_state_attrs
        })
        new_state_id = create_res["id"]

        now_ms = str(int(asyncio.get_running_loop().time() * 1000))

        # ✅ FIX: Use a URL-safe separator for the association type.
        await self.api.boss.put_association({
            "type": f"handshake_state-{role}-{peer_id}",
            "sourceId": self_identity_id, "targetId": new_state_id,
            "time": now_ms, "position": now_ms, "attrs": {}
        })
        
        await self.api.boss.put_association({
            "type": "has_handshake_state",
            "sourceId": self_identity_id, "targetId": new_state_id,
            "time": now_ms, "position": now_ms, "attrs": {}
        })

        new_state_obj = await self.api.boss.get_object(ElmType.HandshakeState, new_state_id)
        return new_state_obj, True

    async def update_role_state(self, role: str, peer_id: str, fields: Dict[str, Any]):
        """
        Updates an existing HandshakeState object using optimistic locking.
        """
        state_obj = await self._find_handshake_state_object(role, peer_id)
        if not state_obj:
            raise RuntimeError(f"Attempted to update a non-existent state for peer {peer_id}")
        
        state_obj["attrs"].update(fields)
        
        await self.api.boss.put_object({
            "id": state_obj["id"],
            "type": ElmType.HandshakeState,
            "version": state_obj["version"],
            "attrs": state_obj["attrs"]
        })

