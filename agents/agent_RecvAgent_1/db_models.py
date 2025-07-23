from db_sdk import Model, Field

class Message(Model):
    __tablename__ = "messages"
    id        : int    = Field("INTEGER", primary_key=True)
    address   : str    = Field("TEXT")
    content   : str    = Field("TEXT")
    timestamp : str    = Field("DATETIME", default="CURRENT_TIMESTAMP")
