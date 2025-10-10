# main_agent.py

# ---- Section 1: Imports ----------------------------------------------------
# Import standard libraries for async, system interaction, and data handling.
import asyncio
import uuid
import json
import argparse
import os
import sys
from typing import Union, Dict, Any

# Import Summoner client and protocol definitions.
from summoner.client import SummonerClient
from summoner.protocol.process import Direction
# Import Pydantic for validation checks.
from pydantic import ValidationError
# Import PyCryptodome for cryptographic operations.
from Crypto.PublicKey import RSA

# Import custom utility functions (assuming crypto_utils.py exists).
try:
    from crypto_utils import (
        load_private_key,
        load_public_key,
        sign_message,
        verify_signature,
    )
except ImportError:
    print("Error: crypto_utils.py not found.")
    sys.exit(1)


# Import the newly created game engine and data models.
try:
    from game_utils import GameEngine
    from models import (
        JsonRpcRequest, SignedWrapper, BatchReportRequest, BatchReportParams,
        MoveParams, PlayerShootParams, GameStartParams, PlayerJoinParams
    )
except ImportError:
    print("Error: game_utils.py or models.py not found.")
    sys.exit(1)

# ---- Section 2: Argument Parsing and Setup ---------------------------------
# Read command-line arguments for player identity.
parser = argparse.ArgumentParser(description="Run a game client with a specific identity.")
parser.add_argument('--name', type=str, required=True, help='The name of the key directory to use for this client (e.g., alice).')
args = parser.parse_args()

# ---- Section 3: Agent Global Setup -----------------------------------------
# Create an asynchronous queue for communication between the GameEngine (input) and the network (send_handler).
action_queue = asyncio.Queue()
# Instantiate the Summoner client.
client = SummonerClient(name=f"GameAgent_{str(uuid.uuid4())[:8]}")
# Placeholder for the GameEngine instance, initialized in the main loop.
game_engine: GameEngine = None

# ---- Section 4: Cryptographic Setup ----------------------------------------
# Construct the path to the key files.
key_directory = os.path.join("keys", args.name)
private_key_path = os.path.join(key_directory, "client_private_key.pem")
public_key_path = os.path.join(key_directory, "client_public_key.pem")

# Check if key files exist.
if not os.path.exists(private_key_path) or not os.path.exists(public_key_path):
    print(f"Error: Key files not found for identity '{args.name}' in '{key_directory}'.")
    print(f"Please generate them first by running: python login.py --name {args.name}")
    sys.exit(1)

# Load client's keys.
CLIENT_PRIVATE_KEY = load_private_key(private_key_path)
CLIENT_PUBLIC_KEY_STR = load_public_key(public_key_path).export_key().decode('utf-8')

# Construct path to server's public key.
server_public_key_path = os.path.join("keys", "server_public_key.pem")

# Check if server key file exists.
if not os.path.exists(server_public_key_path):
    print(f"Error: Server's public key not found at '{server_public_key_path}'.")
    sys.exit(1)

# Load server's public key for trusting batch reports.
SERVER_PUBLIC_KEY = load_public_key(server_public_key_path)
SERVER_PUBLIC_KEY_STR = SERVER_PUBLIC_KEY.export_key().decode('utf-8')


# ---- Section 5: Cryptographic Hooks ----------------------------------------
# Hooks intercept messages for signing (outgoing) and verification (incoming).

@client.hook(direction=Direction.SEND, priority=1)
async def sign_outgoing_message(payload: dict) -> dict:
    """Intercepts outgoing messages to add a digital signature."""
    try:
        # Convert payload to a consistent string format for signing.
        payload_bytes = json.dumps(payload, sort_keys=True).encode('utf-8')
        # Sign the message bytes with our private key.
        signature = sign_message(payload_bytes, CLIENT_PRIVATE_KEY)
        # Wrap the payload, signature, and public key.
        signed_wrapper = SignedWrapper(payload=payload, signature=signature, public_key=CLIENT_PUBLIC_KEY_STR)
        return signed_wrapper.model_dump()
    except Exception as e:
        print(f"[SIGNING ERROR] Failed to sign outgoing message: {e}")
        return None # Returning None stops the message from being sent.

@client.hook(direction=Direction.RECEIVE, priority=1)
async def verify_incoming_message(payload: dict) -> Union[dict, None]:
    """Intercepts incoming messages to verify authenticity."""
    # Extract the actual content, handling potential server wrapping.
    message_to_validate = payload.get('content') if isinstance(payload, dict) and 'content' in payload else payload
    if not isinstance(message_to_validate, dict): return None
    try:
        # Validate the SignedWrapper structure.
        signed_wrapper = SignedWrapper.model_validate(message_to_validate)
        # Import the sender's public key.
        sender_public_key = RSA.import_key(signed_wrapper.public_key.encode('utf-8'))
        # Recreate the exact original message string.
        original_payload_bytes = json.dumps(signed_wrapper.payload, sort_keys=True).encode('utf-8')
        # Verify the signature.
        if not verify_signature(original_payload_bytes, signed_wrapper.signature, sender_public_key):
            return None # Invalid signature, discard the message.
        # If valid, pass the verified payload and sender identity to the receiver.
        return {"identity": signed_wrapper.public_key, "payload": signed_wrapper.payload}
    except (ValidationError, Exception):
        # If validation or verification fails (e.g., bad key format, bad signature format), discard the message.
        return None

# ---- Section 6: Core Agent Handlers ----------------------------------------

@client.receive(route="")
async def receiver_handler(msg: dict) -> None:
    """
    Handles verified batch reports from the trusted server, individually verifies
    each event within the batch, and then passes the cleaned batch to the
    GameEngine for processing.
    """
    if not isinstance(msg, dict) or "payload" not in msg or "identity" not in msg or game_engine is None:
        return

    # --- Trust Verification: Ensure the batch report is from the server ---
    # Only process messages that are batch reports.
    if msg["payload"].get("method") != "batch.report":
        return  # Ignore non-report messages at this handler.

    # Verify that the sender's public key matches our trusted server key.
    if msg["identity"] != SERVER_PUBLIC_KEY_STR:
        print("[SECURITY WARNING] Discarding batch report from untrusted source.")
        return

    # If we reach here, the batch report's signature has been verified by the
    # 'verify_incoming_message' hook, and the sender's identity is confirmed
    # to be the trusted server. Now, we process the contents.
    try:
        report = BatchReportRequest.model_validate(msg["payload"])
        
        # --- Individually verify each event in the batch ---
        verified_events = []
        for event_dict in report.params.deltaEvents:
            try:
                # 1. Validate the structure of the inner message
                signed_event = SignedWrapper.model_validate(event_dict)
                
                # 2. Recreate the exact original payload string for verification
                original_payload_bytes = json.dumps(signed_event.payload, sort_keys=True).encode('utf-8')
                
                # 3. Import the sender's public key
                sender_public_key = RSA.import_key(signed_event.public_key.encode('utf-8'))

                # 4. Verify the signature
                if verify_signature(original_payload_bytes, signed_event.signature, sender_public_key):
                    verified_events.append(event_dict) # Append the original dict if valid
            except (ValidationError, Exception):
                # If the inner event is malformed or its signature is invalid, discard it silently.
                continue
        
        # --- Use the new, cleaned list of events ---
        # Create a new BatchReportParams object with only the verified events.
        # This ensures the game engine only processes authenticated actions.
        verified_report_params = BatchReportParams(
            frameNumber=report.params.frameNumber,
            deltaEvents=verified_events,
            deltaTiming=report.params.deltaTiming
        )

        # Pass the cleaned report parameters to the game engine.
        game_engine.process_report(verified_report_params, action_queue)

    except ValidationError:
        # Ignore malformed outer reports.
        return


@client.send(route="")
async def send_handler() -> dict:
    """Waits for actions on the queue (populated by the GameEngine) and formats them for sending."""
    # Wait for the GameEngine to put an action on the queue.
    action = await action_queue.get()
    request_model = None

    # Construct the appropriate JSON-RPC request based on the action type.
    if action["type"] == "join":
        request_model = JsonRpcRequest(method="player.join", params=PlayerJoinParams())
    elif action["type"] == "start":
        request_model = JsonRpcRequest(method="game.start", params=GameStartParams())
    elif action["type"] == "move":
        params = MoveParams(direction=action["dir"])
        request_model = JsonRpcRequest(method="player.move", params=params)
    elif action["type"] == "shoot":
        params = PlayerShootParams(target_x=action["target"][0], target_y=action["target"][1])
        request_model = JsonRpcRequest(method="player.shoot", params=params)

    if request_model:
        # Return the dictionary. The 'sign_outgoing_message' hook will wrap this.
        return request_model.model_dump()
    else:
        # If an unknown action type was received, skip this cycle and wait for the next valid action.
        return await send_handler()


# ---- Section 7: Main Application Loop --------------------------------------

async def main_loop():
    """The main loop that drives the GameEngine for input and rendering."""
    global game_engine
    # Initialize the GameEngine now that the crypto setup is complete.
    game_engine = GameEngine(player_name=args.name, client_public_key=CLIENT_PUBLIC_KEY_STR)

    running = True
    while running:
        try:
            # Handle input using the GameEngine and populate the action_queue.
            # This is a synchronous call that interacts with the async queue using create_task internally.
            running = game_engine.handle_input(action_queue)

            # Render the current state using the GameEngine.
            game_engine.render()

            # CRITICAL: Yield control to the asyncio event loop. This allows background
            # networking tasks (send_handler and receiver_handler) to run concurrently.
            # Aiming for ~60fps.
            await asyncio.sleep(0.016)
        except asyncio.CancelledError:
            running = False

    print("Shutdown initiated...")
    # Clean up tasks when the loop finishes (e.g., when the user closes the window).
    current_task = asyncio.current_task()
    for task in asyncio.all_tasks():
        if task is not current_task: task.cancel()

# ---- Section 8: Main Execution Block ---------------------------------------
if __name__ == "__main__":
    try:
        # Get the main asyncio event loop.
        loop = asyncio.get_event_loop()
        # Start the main application loop as a background task.
        loop.create_task(main_loop())
        # Immediately send an initial 'join' message to announce our presence.
        loop.call_soon(lambda: asyncio.create_task(action_queue.put({"type": "join"})))
        # Start the Summoner client. This is a blocking call that runs the networking loop.
        client.run(host="127.0.0.1", port=8888)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("Shutdown requested.")
    finally:
        # Ensure the GameEngine (and Pygame) shuts down cleanly when the client stops.
        if game_engine:
            game_engine.shutdown()
        print("Client shut down cleanly.")