# agents/agent_HSAgent_2/api.py

import asyncio
from typing import Dict, Any, Optional, Tuple

# We depend entirely on the high-level, battle-tested API client.
from adapt import SummonerAPIClient, APIError

# ---[ ElmTypes - FINAL VERSION ]---
# These constants define the "types" of our objects in the BOSS substrate.
class ElmType:
    AgentIdentity = 7001
    AgentSecretVault = 7002
    NonceMarker = 7004
    HandshakeSendState = 7005      # [NEW] For the proactive "send" FSM
    HandshakeReceiveState = 7006   # [NEW] For the reactive "receive" FSM

# =============================================================================
#  The HybridNonceStore: The Notary's Office (Stable)
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
            "chainName": f"nonce-journal-{self.self_id}-{self.peer_id}",
            "shardId": 0
        }
        self.index_source_id: Optional[str] = None

    async def _get_index_source_id(self) -> str:
        """Helper to lazily fetch the agent's own identity ID for the index."""
        if self.index_source_id:
            return self.index_source_id
        
        # Use the efficient index to look up our own identity object ID.
        assoc_response = await self.api.boss.get_associations(
            self.self_id, self.api.user_id, {"limit": 1}
        )
        if not assoc_response or not assoc_response.get("associations"):
             raise RuntimeError(f"Could not find AgentIdentity for agent {self.self_id}")
        self.index_source_id = assoc_response["associations"][0]["targetId"]
        return self.index_source_id

    async def exists(self, nonce: str) -> bool:
        """The QUERY side. Performs a single, high-speed query against BOSS."""
        source_id = await self._get_index_source_id()
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
        """The COMMAND side. Writes to both the Fathom journal and the BOSS index."""
        source_id = await self._get_index_source_id()
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
    
    async def delete_journal(self) -> None:
        """The CLEANUP side. Deletes the entire Fathom chain for the journal."""
        try:
            await self.api.chains.delete(self.journal_chain_key)
        except APIError as e:
            if e.status_code != 404:
                raise

# =============================================================================
#  The SubstrateStateStore: The Agent's New Brain (FINALIZED)
# =============================================================================

# Define which fields belong to which of our segregated state objects. This is
# the key to preventing write conflicts between the agent's concurrent loops.
SEND_STATE_FIELDS = {"state", "local_nonce", "local_reference", "exchange_count", "finalize_retry_count"}
RECV_STATE_FIELDS = {"peer_nonce", "peer_reference", "peer_address"}

class SubstrateStateStore:
    """
    [FINAL VERSION] This is the transparent facade for the agent's state. It
    exposes a single, unified view of a "HandshakeState", but internally it
    manages two separate, conflict-free BOSS objects to eliminate race
    conditions between the agent's send and receive logic.
    """
    def __init__(self, api: SummonerAPIClient, self_agent_id: str):
        self.api = api
        self.self_agent_id = self_agent_id
        self._identity_id_cache: Optional[str] = None

    async def _get_self_identity_id(self) -> str:
        """Helper to find and cache our own AgentIdentity object ID using the fast index."""
        if self._identity_id_cache:
            return self._identity_id_cache

        assoc_response = await self.api.boss.get_associations(self.self_agent_id, self.api.user_id)
        if not assoc_response or not assoc_response.get("associations"):
            raise RuntimeError(f"Could not find AgentIdentity object for self via index lookup for agent {self.self_agent_id}")
        self._identity_id_cache = assoc_response["associations"][0]["targetId"]
        return self._identity_id_cache

    async def _find_peer_identity_id(self, peer_agent_uuid: str) -> Optional[str]:
        """Finds a peer's AgentIdentity object ID by performing a direct, indexed query."""
        try:
            assoc_response = await self.api.boss.get_associations(peer_agent_uuid, self.api.user_id, {"limit": 1})
            if assoc_response and assoc_response.get("associations"):
                return assoc_response["associations"][0]["targetId"]
        except APIError as e:
            if e.status_code == 404: return None
            raise
        return None

    async def _find_state_objects(self, role: str, peer_id: str) -> Tuple[Optional[Dict], Optional[Dict]]:
        """A helper to find BOTH the send and receive state objects concurrently."""
        self_identity_id = await self._get_self_identity_id()
        send_assoc_type = f"hs_send_state-{role}-{peer_id}"
        recv_assoc_type = f"hs_recv_state-{role}-{peer_id}"

        async def get_obj_by_assoc(assoc_type: str, obj_type: int):
            try:
                assoc_res = await self.api.boss.get_associations(assoc_type, self_identity_id)
                if assoc_res and assoc_res.get("associations"):
                    target_id = assoc_res["associations"][0]["targetId"]
                    return await self.api.boss.get_object(obj_type, target_id)
            except APIError as e:
                if e.status_code == 404: return None
                raise
            return None

        send_obj, recv_obj = await asyncio.gather(
            get_obj_by_assoc(send_assoc_type, ElmType.HandshakeSendState),
            get_obj_by_assoc(recv_assoc_type, ElmType.HandshakeReceiveState)
        )
        return send_obj, recv_obj

    async def ensure_role_state(self, role: str, peer_id: str, default_state: str) -> Tuple[Dict[str, Any], bool]:
        """Finds or creates BOTH state objects and returns a single, merged dictionary."""
        send_obj, recv_obj = await self._find_state_objects(role, peer_id)
        created = False
        self_identity_id = await self._get_self_identity_id()
        now_ms = str(int(asyncio.get_running_loop().time() * 1000))

        if not send_obj:
            created = True
            attrs = {"role": role, "peerId": peer_id, "state": default_state, **{k: None for k in (SEND_STATE_FIELDS - {'state'})}}
            attrs["exchange_count"] = 0
            attrs["finalize_retry_count"] = 0
            res = await self.api.boss.put_object({"type": ElmType.HandshakeSendState, "version": 0, "attrs": attrs})
            send_id = res["id"]
            await asyncio.gather(
                self.api.boss.put_association({
                    "type": f"hs_send_state-{role}-{peer_id}", "sourceId": self_identity_id, "targetId": send_id,
                    "time": now_ms, "position": now_ms, "attrs": {}
                }),
                self.api.boss.put_association({
                    "type": "has_handshake_with", "sourceId": self_identity_id, "targetId": peer_id,
                    "time": now_ms, "position": now_ms, "attrs": {"role": role}
                })
            )
            send_obj = await self.api.boss.get_object(ElmType.HandshakeSendState, send_id)

        if not recv_obj:
            created = True
            attrs = {k: None for k in RECV_STATE_FIELDS}
            res = await self.api.boss.put_object({"type": ElmType.HandshakeReceiveState, "version": 0, "attrs": attrs})
            recv_id = res["id"]
            await self.api.boss.put_association({
                "type": f"hs_recv_state-{role}-{peer_id}", "sourceId": self_identity_id, "targetId": recv_id,
                "time": now_ms, "position": now_ms, "attrs": {}
            })
            recv_obj = await self.api.boss.get_object(ElmType.HandshakeReceiveState, recv_id)

        merged_attrs = {**recv_obj["attrs"], **send_obj["attrs"]}
        return {"attrs": merged_attrs, "id": send_obj["id"], "version": send_obj["version"]}, created

    async def update_role_state(self, role: str, peer_id: str, fields: Dict[str, Any]):
        """The 'smart router'. It inspects fields and only writes to the appropriate underlying state object."""
        send_fields = {k: v for k, v in fields.items() if k in SEND_STATE_FIELDS}
        recv_fields = {k: v for k, v in fields.items() if k in RECV_STATE_FIELDS}

        tasks = []
        if send_fields:
            async def update_send():
                send_obj, _ = await self._find_state_objects(role, peer_id)
                if not send_obj: raise RuntimeError(f"Send state not found for {role}/{peer_id}")
                send_obj["attrs"].update(send_fields)
                await self.api.boss.put_object({
                    "id": send_obj["id"], "type": ElmType.HandshakeSendState,
                    "version": send_obj["version"], "attrs": send_obj["attrs"]
                })
            tasks.append(update_send())

        if recv_fields:
            async def update_recv():
                _, recv_obj = await self._find_state_objects(role, peer_id)
                if not recv_obj: raise RuntimeError(f"Receive state not found for {role}/{peer_id}")
                recv_obj["attrs"].update(recv_fields)
                await self.api.boss.put_object({
                    "id": recv_obj["id"], "type": ElmType.HandshakeReceiveState,
                    "version": recv_obj["version"], "attrs": recv_obj["attrs"]
                })
            tasks.append(update_recv())
        
        if tasks:
            await asyncio.gather(*tasks)

    async def find_role_states(self, role: str) -> list[Dict[str, Any]]:
        """Finds all conversations for a given role by discovering peers and merging their state objects."""
        self_identity_id = await self._get_self_identity_id()
        associations = await self.api.boss.get_associations("has_handshake_with", self_identity_id, {"limit": 1000})
        if not associations.get("associations"):
            return []
        
        peer_ids = [a["targetId"] for a in associations["associations"] if a.get("attrs", {}).get("role") == role]
        if not peer_ids:
            return []

        state_pairs = await asyncio.gather(*[self._find_state_objects(role, pid) for pid in peer_ids])
        
        merged_states = []
        for send_obj, recv_obj in state_pairs:
            if send_obj and recv_obj:
                merged_attrs = {**recv_obj["attrs"], **send_obj["attrs"]}
                merged_states.append({"attrs": merged_attrs, "id": send_obj["id"], "version": send_obj["version"]})
        
        return merged_states