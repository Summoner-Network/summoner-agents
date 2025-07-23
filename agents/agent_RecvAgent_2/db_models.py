from db_sdk import Model, Field

class Message(Model):
    __tablename__ = "messages"
    id          = Field("INTEGER", primary_key=True)
    sender_id   = Field("TEXT", nullable=False, check="length(sender_id)>0")
    content     = Field("TEXT")
    timestamp   = Field("DATETIME", default="CURRENT_TIMESTAMP", nullable=False)

class Validation(Model):
    __tablename__ = "validations"
    id          = Field("INTEGER", primary_key=True)
    address     = Field("TEXT", nullable=False)
    sender_id   = Field("TEXT")
    is_valid    = Field("INTEGER", default=0, check="is_valid IN (0,1)", nullable=False)
    timestamp   = Field("DATETIME", default="CURRENT_TIMESTAMP", nullable=False)

class BannedAddress(Model):
    __tablename__ = "banned_addresses"
    id          = Field("INTEGER", primary_key=True)
    address     = Field("TEXT", nullable=False)
    timestamp   = Field("DATETIME", default="CURRENT_TIMESTAMP", nullable=False)
