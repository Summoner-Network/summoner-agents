# agents/agent_HSAgent_2/selftest.py

import asyncio
import os
import uuid
import json
from pathlib import Path
import assertpy
from typing import Dict, Any

# This test assumes it's run from a context where the `summoner` package
# and the local `api.py` module are importable.
from adapt import SummonerAPIClient, APIError
from api import SubstrateStateStore, HybridNonceStore, ElmType

# =============================================================================
#  THE TEST CASES (FINAL, REFACTORED VERSION)
# =============================================================================

async def test_state_and_nonce_lifecycle(api: SummonerAPIClient, self_id: str):
    """
    Tests the core "happy path" functionality of the adapters, ensuring it
    correctly handles the split-state model and valid object IDs.
    """
    print("  [RUNNING] test_state_and_nonce_lifecycle (Happy Path)...")
    state_store = SubstrateStateStore(api, self_id)
    role = "initiator"
    peer_identity_id = None

    try:
        # Provision a valid AgentIdentity for the peer to get a real object ID.
        peer_res = await api.boss.put_object({
            "type": ElmType.AgentIdentity, "version": 0, "attrs": {"agentId": str(uuid.uuid4())}
        })
        peer_identity_id = peer_res["id"]
        peer_id = peer_identity_id # Use the valid object ID for all operations.

        # 1. Create state.
        state_obj, created = await state_store.ensure_role_state(role, peer_id, "init_ready")
        assertpy.assert_that(created).is_true()
        assertpy.assert_that(state_obj).is_not_none()
        original_version = state_obj["version"]

        # 2. Update state.
        await state_store.update_role_state(role, peer_id, {"state": "init_exchange", "local_nonce": "abc123"})

        # 3. Read state back and verify.
        send_obj, recv_obj = await state_store._find_state_objects(role, peer_id)
        merged_attrs = {**recv_obj["attrs"], **send_obj["attrs"]}
        
        assertpy.assert_that(merged_attrs["state"]).is_equal_to("init_exchange")
        assertpy.assert_that(send_obj["version"]).is_greater_than(original_version)

        # 4. Test Nonce Store functionality.
        nonce_store = HybridNonceStore(api, self_id, peer_id)
        test_nonce = "xyz789"
        from datetime import datetime, timezone
        assertpy.assert_that(await nonce_store.exists(test_nonce)).is_false()
        await nonce_store.add(test_nonce, datetime.now(timezone.utc))
        assertpy.assert_that(await nonce_store.exists(test_nonce)).is_true()

        print("  [SUCCESS] test_state_and_nonce_lifecycle")
        
    finally:
        # Cleanup: Remove the temporary peer identity object.
        if peer_identity_id:
            await api.boss.remove_object(ElmType.AgentIdentity, peer_identity_id)


async def test_error_handling(api: SummonerAPIClient, self_id: str):
    """
    Tests how the adapters handle expected error conditions.
    """
    print("  [RUNNING] test_error_handling (Unhappy Paths)...")
    state_store = SubstrateStateStore(api, self_id)
    
    # Test updating a state for a peer that looks valid but doesn't exist.
    non_existent_peer_id = "999999999999999"
    try:
        await state_store.update_role_state("initiator", non_existent_peer_id, {"state": "error"})
        assertpy.fail("Expected RuntimeError when updating non-existent state")
    except RuntimeError:
        pass

    print("  [SUCCESS] test_error_handling")


async def test_concurrency_safety(api: SummonerAPIClient, self_id: str):
    """
    Validates the optimistic locking mechanism on the underlying state objects.
    """
    print("  [RUNNING] test_concurrency_safety (Optimistic Locking)...")
    state_store = SubstrateStateStore(api, self_id)
    role = "responder"
    peer_identity_id = None

    try:
        # Provision a valid peer identity.
        peer_res = await api.boss.put_object({
            "type": ElmType.AgentIdentity, "version": 0, "attrs": {"agentId": str(uuid.uuid4())}
        })
        peer_identity_id = peer_res["id"]
        peer_id = peer_identity_id

        # 1. Create the initial state objects.
        await state_store.ensure_role_state(role, peer_id, "start")
        
        # 2. Fetch the underlying 'send' object to get its current version.
        send_obj, _ = await state_store._find_state_objects(role, peer_id)

        # 3. Simulate a race condition: create two payloads that try to update the
        #    SAME object with the SAME (now stale) version number.
        clash_payload_1 = {"id": send_obj["id"], "type": ElmType.HandshakeSendState, "version": send_obj["version"], "attrs": {**send_obj["attrs"], "local_nonce": "clash_1"}}
        clash_payload_2 = {"id": send_obj["id"], "type": ElmType.HandshakeSendState, "version": send_obj["version"], "attrs": {**send_obj["attrs"], "local_nonce": "clash_2"}}
        
        results = await asyncio.gather(
            api.boss.put_object(clash_payload_1),
            api.boss.put_object(clash_payload_2),
            return_exceptions=True
        )
        
        # 4. Verify that one update succeeded and one failed with a version clash error (HTTP 500).
        successes = [r for r in results if not isinstance(r, Exception)]
        failures = [r for r in results if isinstance(r, APIError)]

        assertpy.assert_that(successes).is_length(1)
        assertpy.assert_that(failures).is_length(1)
        assertpy.assert_that(failures[0].status_code).is_equal_to(500)

        print("  [SUCCESS] test_concurrency_safety")
    finally:
        if peer_identity_id:
            await api.boss.remove_object(ElmType.AgentIdentity, peer_identity_id)


async def test_security_and_isolation(api: SummonerAPIClient, self_id: str):
    """
    Verifies that one user ("Bob") cannot see or access the state objects
    created by another user ("Alice").
    """
    print("  [RUNNING] test_security_and_isolation (Tenant Walls)...")
    alice = api
    peer_identity_id = None
    bob_identity_id = None
    
    # Create Bob, a separate user in a separate session.
    bob = SummonerAPIClient(alice._client.base_url)
    await bob.login({
        "username": f"bob-{uuid.uuid4().hex[:8]}",
        "password": "password"
    })

    try:
        # 1. Alice creates a state object in her own tenant space.
        alice_store = SubstrateStateStore(alice, self_id)
        peer_res = await alice.boss.put_object({
            "type": ElmType.AgentIdentity, "version": 0, "attrs": {"agentId": str(uuid.uuid4())}
        })
        peer_identity_id = peer_res["id"]
        await alice_store.ensure_role_state("initiator", peer_identity_id, "alice_state")
        
        # 2. Bob needs his own identity object provisioned to even attempt a lookup.
        bob_agent_id = str(uuid.uuid4())
        bob_identity_res = await bob.boss.put_object({
            "type": ElmType.AgentIdentity, "version": 0, "attrs": {"agentId": bob_agent_id, "ownerId": bob.user_id}
        })
        bob_identity_id = bob_identity_res["id"]
        
        # 3. Bob gets his own index associations created correctly.
        now_ms = str(int(asyncio.get_running_loop().time() * 1000))
        await asyncio.gather(
            bob.boss.put_association({
                "type": "owns_agent_identity", "sourceId": bob.user_id, "targetId": bob_identity_id,
                "time": now_ms, "position": now_ms, "attrs": {"agentId": bob_agent_id}
            }),
            bob.boss.put_association({
                "type": bob_agent_id, "sourceId": bob.user_id, "targetId": bob_identity_id,
                "time": now_ms, "position": now_ms, "attrs": {}
            })
        )

        # 4. Bob now tries to find Alice's state object using Alice's peer ID.
        #    Because he is authenticated as Bob, the query will be scoped to his
        #    tenant, and he should find nothing.
        bob_store = SubstrateStateStore(bob, bob_agent_id)
        send_obj, recv_obj = await bob_store._find_state_objects("initiator", peer_identity_id)
        assertpy.assert_that(send_obj).is_none()
        assertpy.assert_that(recv_obj).is_none()

    finally:
        # Cleanup all temporary objects.
        if peer_identity_id: await alice.boss.remove_object(ElmType.AgentIdentity, peer_identity_id)
        if bob_identity_id and bob: await bob.boss.remove_object(ElmType.AgentIdentity, bob_identity_id)
        if bob: await bob.close()

    print("  [SUCCESS] test_security_and_isolation")


# =============================================================================
#  THE TEST RUNNER (FINALIZED)
# =============================================================================

async def runSelfTests(base_url: str, auth_creds: Dict):
    """
    The main, importable entrypoint for the self-test suite.
    """
    print("======================================================")
    print("  Running HSAgent Substrate Adapter Self-Tests")
    print("======================================================")

    if not base_url or not auth_creds:
        raise ValueError("base_url and auth_creds are required.")

    owner_client = SummonerAPIClient(base_url)
    self_agent_id = str(uuid.uuid4())
    identity_id = None

    try:
        # Setup: Log in and provision a primary test identity with proper indexing.
        await owner_client.login(auth_creds)
        print(f"[Setup] Logged in as primary user: {owner_client.username}")

        identity_res = await owner_client.boss.put_object({
            "type": ElmType.AgentIdentity, "version": 0, "attrs": {"agentId": self_agent_id, "ownerId": owner_client.user_id}
        })
        identity_id = identity_res["id"]
        
        now_ms = str(int(asyncio.get_running_loop().time() * 1000))
        await asyncio.gather(
            owner_client.boss.put_association({
                "type": "owns_agent_identity", "sourceId": owner_client.user_id, "targetId": identity_id,
                "time": now_ms, "position": now_ms, "attrs": {"agentId": self_agent_id}
            }),
            owner_client.boss.put_association({
                "type": self_agent_id, "sourceId": owner_client.user_id, "targetId": identity_id,
                "time": now_ms, "position": now_ms, "attrs": {}
            })
        )
        
        # Run the full battery of tests.
        await test_state_and_nonce_lifecycle(owner_client, self_agent_id)
        await test_error_handling(owner_client, self_agent_id)
        await test_concurrency_safety(owner_client, self_agent_id)
        await test_security_and_isolation(owner_client, self_agent_id)

        print("\n✅ ALL ADAPTER SELF-TESTS PASSED ✅")
        return True

    except Exception as e:
        print(f"\n❌ TEST FAILED ❌\nREASON: {e}")
        raise
    finally:
        # Teardown: Clean up all created resources.
        if owner_client:
            if identity_id:
                try:
                    await owner_client.boss.remove_object(ElmType.AgentIdentity, identity_id)
                    print("[Teardown] Cleaned up test agent identity object.")
                except APIError as e:
                    print(f"[Teardown WARNING] Failed to clean up test agent identity: {e}")
            await owner_client.close()
        print("\n[Teardown] Self-test complete.")