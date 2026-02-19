from db_sdk import Model, Field

class RoleState(Model):
    """
    Per-peer, per-role state row.
    Uniqueness of a conversation thread is enforced by the composite index
    (self_id, role, peer_id) created at startup.
    """
    __tablename__ = "role_state"

    id                   = Field("INTEGER", primary_key=True)

    # Identity / partitioning
    self_id              = Field("TEXT", nullable=False)  # this agent
    role                 = Field("TEXT", nullable=False, check="role IN ('initiator','responder')")
    peer_id              = Field("TEXT", nullable=False)  # the other agent

    # FSM state
    state                = Field("TEXT", nullable=True)   # init_*, resp_*

    # Nonces and references
    local_nonce          = Field("TEXT", nullable=True)
    peer_nonce           = Field("TEXT", nullable=True)
    local_reference      = Field("TEXT", nullable=True)
    peer_reference       = Field("TEXT", nullable=True)

    # Operational counters
    exchange_count       = Field("INTEGER", default=0, nullable=False)
    finalize_retry_count = Field("INTEGER", default=0, nullable=False)

    # Peer address (last seen from server payload)
    peer_address         = Field("TEXT", nullable=True)

    # --- Crypto metadata (optional; used by HSAgent_1) ---
    # Persist the peer's signing pubkey and KX pubkey as seen during handshake,
    # plus timestamps for when the symmetric key was derived and when we last
    # successfully opened a secure envelope.
    peer_sign_pub        = Field("TEXT", nullable=True)
    peer_kx_pub          = Field("TEXT", nullable=True)
    hs_derived_at        = Field("DATETIME", nullable=True)
    last_secure_at       = Field("DATETIME", nullable=True)

    # Timestamps
    created_at           = Field("DATETIME", default="CURRENT_TIMESTAMP", nullable=False)
    updated_at           = Field("DATETIME", on_update=True, nullable=False)


class NonceEvent(Model):
    """
    Append-only nonce log for the *current* conversation with a given peer.
    Clear rows by (self_id, role, peer_id) after final handshake.
    Used both for logging and for replay/TTL checks in HSAgent_1.
    """
    __tablename__ = "nonce_event"

    id         = Field("INTEGER", primary_key=True)

    self_id    = Field("TEXT", nullable=False)
    role       = Field("TEXT", nullable=False, check="role IN ('initiator','responder')")
    peer_id    = Field("TEXT", nullable=False)

    # Direction of the event relative to this agent.
    flow       = Field("TEXT", nullable=False, check="flow IN ('sent','received')")

    # The nonce involved in the event.
    nonce      = Field("TEXT", nullable=False)

    created_at = Field("DATETIME", default="CURRENT_TIMESTAMP", nullable=False)
