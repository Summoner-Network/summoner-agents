from db_sdk import Model, Field

class Message(Model):
    """
    Generic message table used by both the receive and send databases.
    """
    __tablename__ = "messages"

    id         = Field("INTEGER", primary_key=True)
    data       = Field("TEXT", nullable=False)
    state      = Field("TEXT", nullable=False, check="state IN ('new','processed')")
    created_at = Field("TEXT", default="CURRENT_TIMESTAMP", nullable=False)
    updated_at = Field("TEXT", on_update=True, nullable=False)
