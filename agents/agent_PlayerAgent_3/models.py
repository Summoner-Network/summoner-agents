# models.py
# This file defines the Pydantic models used for data validation and communication protocols.

import uuid
from typing import Union, Literal, Dict, Any, List
from pydantic import BaseModel, Field

# ---- Game Action Parameters ------------------------------------------------

# Defines the parameters for a player movement action.
class MoveParams(BaseModel):
    direction: Literal["w", "a", "s", "d"]

# Defines parameters for a shooting action, including the mouse target coordinates.
class PlayerShootParams(BaseModel):
    target_x: int
    target_y: int

# Defines the (empty) parameters for a "start the game" action.
class GameStartParams(BaseModel):
    pass

# Defines the (empty) parameters for a "player joining" action.
class PlayerJoinParams(BaseModel):
    pass

# ---- JSON-RPC 2.0 Structures -----------------------------------------------

# The main JSON-RPC 2.0 request model for game actions.
class JsonRpcRequest(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    method: Literal["player.move", "game.start", "player.join", "player.shoot"]
    params: Union[MoveParams, GameStartParams, PlayerJoinParams, PlayerShootParams]
    id: Union[str, int] = Field(default_factory=lambda: str(uuid.uuid4()))

# Defines the parameters for the batch reports received from the ReportAgent.
class BatchReportParams(BaseModel):
    frameNumber: int
    # deltaEvents contains a list of SignedWrapper dictionaries for that frame.
    deltaEvents: List[Dict[str, Any]]
    # deltaTiming is the time elapsed for the frame (in nanoseconds).
    deltaTiming: int

# The JSON-RPC 2.0 request model for batch reports.
class BatchReportRequest(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    method: Literal["batch.report"]
    params: BatchReportParams
    id: Union[str, int]

# ---- Security Structures ---------------------------------------------------

# A security wrapper for messages, ensuring authenticity and integrity.
class SignedWrapper(BaseModel):
    payload: Dict[str, Any]  # The original message (e.g., a JsonRpcRequest)
    signature: str           # The digital signature of the payload
    public_key: str          # The public key of the sender for verification