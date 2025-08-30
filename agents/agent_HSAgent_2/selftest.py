import asyncio
import os
import uuid
import json
from pathlib import Path
import assertpy
from typing import Dict, Any

# This test assumes it's run from a context where the `summoner` package
# and the local `api.py` module are importable.
from client import SummonerAPIClient, APIError
from api import SubstrateStateStore, HybridNonceStore, ElmType

async def test_state_and_nonce_lifecycle(api: SummonerAPIClient, self_id: str):
    """
    Tests the core functionality of both the SubstrateStateStore
    and the HybridNonceStore working together. This is a full, end-to-end
    validation of the agent's new data layer.
    """
    print("  [RUNNING] test_state_and_nonce_lifecycle...")
    state_store = SubstrateStateStore(api, self_id)
    
    peer_id = str(uuid.uuid4())
    role = "initiator"

    # 1. Test SubstrateStateStore: Ensure State (Create)
    print(f"    - Ensuring state for peer {peer_id[:8]}...")
    state_obj, created = await state_store.ensure_role_state(role, peer_id, "init_ready")
    assertpy.assert_that(created).is_true()
    assertpy.assert_that(state_obj).is_not_none()
    assertpy.assert_that(state_obj["attrs"]["state"]).is_equal_to("init_ready")
    original_version = state_obj["version"]

    # 2. Test SubstrateStateStore: Update State
    print(f"    - Updating state for peer {peer_id[:8]}...")
    await state_store.update_role_state(role, peer_id, {"state": "init_exchange", "local_nonce": "abc123"})

    # 3. Test SubstrateStateStore: Verify Update (Read)
    updated_state_obj = await state_store._find_handshake_state_object(role, peer_id)
    assertpy.assert_that(updated_state_obj).is_not_none()
    assertpy.assert_that(updated_state_obj["attrs"]["state"]).is_equal_to("init_exchange")
    assertpy.assert_that(updated_state_obj["attrs"]["local_nonce"]).is_equal_to("abc123")
    assertpy.assert_that(updated_state_obj["version"]).is_greater_than(original_version)

    # 4. Test HybridNonceStore (CQRS Pattern)
    print(f"    - Testing HybridNonceStore for peer {peer_id[:8]}...")
    nonce_store = HybridNonceStore(api, self_id, peer_id)
    test_nonce = "xyz789"
    from datetime import datetime, timezone

    # a) Check existence (Query side - BOSS)
    exists = await nonce_store.exists(test_nonce)
    assertpy.assert_that(exists).is_false()

    # b) Add nonce (Command side - Fathom + BOSS)
    await nonce_store.add(test_nonce, datetime.now(timezone.utc))
    
    # c) Verify existence
    exists_again = await nonce_store.exists(test_nonce)
    assertpy.assert_that(exists_again).is_true()

    print("  [SUCCESS] test_state_and_nonce_lifecycle")


async def runSelfTests(base_url: str, auth_creds: Dict):
    """
    The main, importable entrypoint for the self-test suite.
    It takes auth credentials and runs a full, live-fire validation
    of the agent's substrate adapters (api.py).
    """
    print("======================================================")
    print("  Running HSAgent Substrate Adapter Self-Tests")
    print("======================================================")

    if not base_url or not auth_creds:
        raise ValueError("base_url and auth_creds are required.")

    owner_client = SummonerAPIClient(base_url)
    self_agent_id = str(uuid.uuid4())
    identity_id = None # To track the created object for cleanup

    try:
        await owner_client.login(auth_creds)
        print(f"[Setup] Logged in as primary user: {owner_client.username}")

        # SETUP: Provision a dummy AgentIdentity object and the necessary
        # associations for the test agent so the adapter's helpers can find it.
        identity_res = await owner_client.boss.put_object({
            "type": ElmType.AgentIdentity, "version": 0, "attrs": {"agentId": self_agent_id, "ownerId": owner_client.user_id}
        })
        identity_id = identity_res["id"]
        
        await owner_client.boss.put_association({
            "type": "owns_agent_identity", "sourceId": owner_client.user_id,
            "targetId": identity_id, "attrs": {"agentId": self_agent_id},
            "time": str(int(asyncio.get_running_loop().time() * 1000)),
            "position": str(int(asyncio.get_running_loop().time() * 1000)),
        })
        
        # Run the actual tests
        await test_state_and_nonce_lifecycle(owner_client, self_agent_id)

        print("\n✅ ALL ADAPTER SELF-TESTS PASSED ✅")
        return True

    except Exception as e:
        print(f"\n❌ TEST FAILED ❌\nREASON: {e}")
        # Re-raise the exception to signal failure to the calling agent.
        raise
    finally:
        if owner_client:
            # TEARDOWN: Clean up the identity object created for the test.
            if identity_id:
                try:
                    await owner_client.boss.remove_object(ElmType.AgentIdentity, identity_id)
                    print("[Teardown] Cleaned up test agent identity object.")
                except APIError as e:
                    print(f"[Teardown WARNING] Failed to clean up test agent identity: {e}")
            await owner_client.close()
        print("\n[Teardown] Self-test complete.")

