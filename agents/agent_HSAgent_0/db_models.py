from db_sdk import Model, Field

class RoleState(Model):
    """
    Per-peer, per-role state row.
    """
    __tablename__ = "role_state"
    id                   = Field("INTEGER", primary_key=True)
    self_id              = Field("TEXT", nullable=False)             # this agent
    role                 = Field("TEXT", nullable=False, check="role IN ('initiator','responder')")
    peer_id              = Field("TEXT", nullable=False)             # the other agent
    state                = Field("TEXT", nullable=True)              # init_*, resp_*
    local_nonce          = Field("TEXT", nullable=True)
    peer_nonce           = Field("TEXT", nullable=True)
    local_reference      = Field("TEXT", nullable=True)
    peer_reference       = Field("TEXT", nullable=True)
    exchange_count       = Field("INTEGER", default=0, nullable=False)
    finalize_retry_count = Field("INTEGER", default=0, nullable=False)
    peer_address         = Field("TEXT", nullable=True)
    created_at           = Field("DATETIME", default="CURRENT_TIMESTAMP", nullable=False)
    updated_at           = Field("DATETIME", on_update=True, nullable=False)

class NonceEvent(Model):
    """
    Append-only nonce log for the *current* conversation with a given peer.
    Clear rows by (self_id, role, peer_id) after final handshake.
    """
    __tablename__ = "nonce_event"
    id         = Field("INTEGER", primary_key=True)
    self_id    = Field("TEXT", nullable=False)
    role       = Field("TEXT", nullable=False, check="role IN ('initiator','responder')")
    peer_id    = Field("TEXT", nullable=False)
    flow       = Field("TEXT", nullable=False, check="flow IN ('sent','received')")
    nonce      = Field("TEXT", nullable=False)
    created_at = Field("DATETIME", default="CURRENT_TIMESTAMP", nullable=False)
