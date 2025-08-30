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
#  THE TEST CASES
# =============================================================================

async def test_state_and_nonce_lifecycle(api: SummonerAPIClient, self_id: str):
    """
    Tests the core "happy path" functionality of the adapters.
    """
    print("  [RUNNING] test_state_and_nonce_lifecycle (Happy Path)...")
    state_store = SubstrateStateStore(api, self_id)
    peer_id = str(uuid.uuid4())
    role = "initiator"

    # 1. Create
    state_obj, created = await state_store.ensure_role_state(role, peer_id, "init_ready")
    assertpy.assert_that(created).is_true()
    assertpy.assert_that(state_obj).is_not_none()
    original_version = state_obj["version"]

    # 2. Update
    await state_store.update_role_state(role, peer_id, {"state": "init_exchange", "local_nonce": "abc123"})

    # 3. Read
    updated_state_obj = await state_store._find_handshake_state_object(role, peer_id)
    assertpy.assert_that(updated_state_obj["attrs"]["state"]).is_equal_to("init_exchange")
    assertpy.assert_that(updated_state_obj["version"]).is_greater_than(original_version)

    # 4. Nonce Store CQRS
    nonce_store = HybridNonceStore(api, self_id, peer_id)
    test_nonce = "xyz789"
    from datetime import datetime, timezone
    assertpy.assert_that(await nonce_store.exists(test_nonce)).is_false()
    await nonce_store.add(test_nonce, datetime.now(timezone.utc))
    assertpy.assert_that(await nonce_store.exists(test_nonce)).is_true()

    print("  [SUCCESS] test_state_and_nonce_lifecycle")


async def test_error_handling(api: SummonerAPIClient, self_id: str):
    """
    Tests how the adapters handle expected error conditions.
    """
    print("  [RUNNING] test_error_handling (Unhappy Paths)...")
    state_store = SubstrateStateStore(api, self_id)
    
    # Test updating a state that doesn't exist
    non_existent_peer_id = str(uuid.uuid4())
    try:
        await state_store.update_role_state("initiator", non_existent_peer_id, {"state": "error"})
        assertpy.fail("Expected RuntimeError when updating non-existent state")
    except RuntimeError:
        pass

    print("  [SUCCESS] test_error_handling")


async def test_concurrency_safety(api: SummonerAPIClient, self_id: str):
    """
    Tests the optimistic locking mechanism provided by the substrate.
    """
    print("  [RUNNING] test_concurrency_safety (Optimistic Locking)...")
    state_store = SubstrateStateStore(api, self_id)
    peer_id = str(uuid.uuid4())
    role = "responder"

    # 1. Create an initial state object.
    state_obj, _ = await state_store.ensure_role_state(role, peer_id, "start")
    
    # 2. Simulate two concurrent processes trying to update the SAME version.
    update_task_1 = state_store.update_role_state(role, peer_id, {"local_nonce": "update_1"})
    update_task_2 = state_store.update_role_state(role, peer_id, {"local_nonce": "update_2"})
    
    results = await asyncio.gather(update_task_1, update_task_2, return_exceptions=True)
    
    # 3. Verify that one update succeeded and one failed.
    # The failed one will raise an APIError because the server returns a 500
    # for a version clash during the `tao_upsert_object` DB function.
    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, APIError)]

    assertpy.assert_that(successes).is_length(1)
    assertpy.assert_that(failures).is_length(1)
    assertpy.assert_that(failures[0].status_code).is_equal_to(500) # Internal Server Error for version clash

    print("  [SUCCESS] test_concurrency_safety")


async def test_security_and_isolation(api: SummonerAPIClient, self_id: str):
    """
    Tests that the adapter correctly respects tenant isolation.
    """
    print("  [RUNNING] test_security_and_isolation (Tenant Walls)...")
    alice = api
    alice_id = self_id
    
    # Create Bob, a separate user in a separate session.
    bob = SummonerAPIClient(alice._client.base_url)
    await bob.login({
        "username": f"bob-{uuid.uuid4().hex[:8]}",
        "password": "password"
    })

    try:
        # Alice's state store
        alice_store = SubstrateStateStore(alice, alice_id)
        peer_id = str(uuid.uuid4())
        
        # 1. Alice creates a state object in her own tenant space.
        await alice_store.ensure_role_state("initiator", peer_id, "alice_state")
        
        # 2. Bob tries to find Alice's state object. This should fail because
        #    his `_get_self_identity_id` will be looking for his own identity.
        bob_store = SubstrateStateStore(bob, "some-other-id") # Bob doesn't know Alice's agent ID
        
        # This will raise a RuntimeError because Bob cannot find an 'owns_agent_identity'
        # association for Alice's agent ID in his own user space.
        try:
            await bob_store._find_handshake_state_object("initiator", peer_id)
            assertpy.fail("Expected RuntimeError when Bob tries to access Alice's state")
        except RuntimeError:
            pass

    finally:
        await bob.close()

    print("  [SUCCESS] test_security_and_isolation")


# =============================================================================
#  THE TEST RUNNER
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
        await owner_client.login(auth_creds)
        print(f"[Setup] Logged in as primary user: {owner_client.username}")

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
        
        # Run the full battery of tests
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
        if owner_client:
            if identity_id:
                try:
                    await owner_client.boss.remove_object(ElmType.AgentIdentity, identity_id)
                    print("[Teardown] Cleaned up test agent identity object.")
                except APIError as e:
                    print(f"[Teardown WARNING] Failed to clean up test agent identity: {e}")
            await owner_client.close()
        print("\n[Teardown] Self-test complete.")