# game_utils.py
# =============================================================================
# MULTIPLAYER GAME ENGINE - COMPREHENSIVE EXAMPLE
# =============================================================================
# This module encapsulates a complete 2D multiplayer game engine with:
#   - Authoritative server architecture (client processes server reports)
#   - Toroidal world (wrap-around edges like Pac-Man or Asteroids)
#   - Camera system with zoom and coordinate transformation
#   - Frame-rate independent physics
#   - Input handling and validation
#   - Collision detection and health management
#   - Real-time rendering with pygame
#
# ARCHITECTURE NOTES:
#   - This is a CLIENT-SIDE engine that receives authoritative game state
#   - The server sends "batch reports" containing validated events
#   - Client applies events to local state and renders the result
#   - Input is sent to server, validated, then applied when report received
# =============================================================================

import pygame
import random
import time
import uuid
import asyncio
import math
from math import sqrt
from typing import Dict, Any, List, Tuple, Optional
from pydantic import ValidationError
from abc import ABC, abstractmethod

# Import the data models for JSON-RPC communication and validation
try:
    from models import JsonRpcRequest, SignedWrapper, BatchReportParams
except ImportError:
    print("Error: models.py not found.")
    exit(1)

# =============================================================================
# GAME INTERFACE DEFINITION
# =============================================================================
# This abstract base class defines the contract that any game engine must
# fulfill. Using an interface ensures loose coupling and makes the code
# testable (you could create a mock engine for unit tests).
# =============================================================================

class IGameEngine(ABC):
    """
    Defines the public interface for a game engine, ensuring that any
    concrete implementation provides the essential methods for managing
    the game's lifecycle.
    
    This follows the Interface Segregation Principle from SOLID design,
    ensuring clients only depend on methods they actually use.
    """

    @abstractmethod
    def process_report(self, report_params: BatchReportParams, action_queue: asyncio.Queue) -> None:
        """
        Processes an authoritative batch report from the server to update game state.
        
        Args:
            report_params: Validated batch report containing events and timing
            action_queue: Queue for sending client actions back to server
            
        This is the core "game tick" - all authoritative state changes happen here.
        """
        pass

    @abstractmethod
    def handle_input(self, action_queue: asyncio.Queue) -> bool:
        """
        Handles user input (keyboard, mouse) and queues actions for the server.
        
        Args:
            action_queue: Queue where user actions are placed for network transmission
            
        Returns:
            bool: True to continue running, False to quit the game
            
        Note: Input is NOT applied locally - it's sent to server for validation.
        This prevents cheating and ensures all clients see the same game state.
        """
        pass

    @abstractmethod
    def render(self) -> None:
        """
        Renders the current game state to the display.
        
        This should be called after process_report() to show the latest state.
        Rendering is separated from logic to allow for variable frame rates.
        """
        pass

    @abstractmethod
    def shutdown(self) -> None:
        """
        Cleanly shuts down the game engine and releases all resources.
        
        This should close windows, uninitialize pygame, and clean up any
        file handles or network connections.
        """
        pass

# =============================================================================
# GAME CONSTANTS
# =============================================================================
# Centralizing constants makes the game easy to tune and prevents magic numbers.
# All speeds are defined "per second" to ensure frame-rate independence.
# =============================================================================

# Display settings
SCREEN_WIDTH = 800                      # Width of the game window in pixels
SCREEN_HEIGHT = 600                     # Height of the game window in pixels

# World geometry - defines the toroidal playing field
WORLD_WIDTH = 3000                      # Total world width (wraps at this boundary)
WORLD_HEIGHT = 3000                     # Total world height (wraps at this boundary)

# Player settings
PLAYER_SIZE = 40                        # Player square size in world units
PLAYER_SPEED_PER_SECOND = 4500          # Movement speed (world units per second)

# Projectile settings
PROJECTILE_SPEED_PER_SECOND = 1000      # Bullet travel speed
PROJECTILE_SIZE = 5                     # Visual size of projectiles
PROJECTILE_LIFETIME_SECONDS = 1.0       # How long bullets exist before despawning

# Game rules
SHOOT_COOLDOWN = 0.20                   # Minimum time between shots (200ms)
MAX_HEALTH = 10                         # Starting health for each player

# =============================================================================
# CAMERA CLASS
# =============================================================================
# Implements a viewport system for a world larger than the screen.
# Handles coordinate transformation between world space and screen space.
# The camera can zoom and pan to follow the player.
# =============================================================================

class Camera:
    """
    Manages the viewport for a toroidal playing field.
    
    COORDINATE SYSTEMS:
        - World coordinates: The actual game world (0 to WORLD_WIDTH/HEIGHT)
        - Screen coordinates: The pixels on the display (0 to SCREEN_WIDTH/HEIGHT)
    
    The camera acts as a "window" into the world, with zoom control.
    Higher zoom values mean you see more of the world (zoomed out).
    """
    
    def __init__(self, screen_width: int, screen_height: int, zoom: float = 2.5):
        """
        Initialize the camera.
        
        Args:
            screen_width: Width of the display in pixels
            screen_height: Height of the display in pixels
            zoom: Zoom level (2.5 means world is 2.5x larger than it appears)
        """
        self.x = 0.0                        # Camera position in world space (top-left corner)
        self.y = 0.0
        self.screen_width = screen_width    # Display dimensions
        self.screen_height = screen_height
        self.zoom = zoom                    # Zoom factor for scaling world to screen
    
    def update(self, target_x: float, target_y: float):
        """
        Center the camera on the target position (typically the player).
        
        Args:
            target_x, target_y: World coordinates to center on
            
        This creates a smooth following effect where the player stays centered.
        """
        # Calculate camera position to center the target on screen
        # We multiply screen dimensions by zoom because world space is larger
        self.x = target_x - (self.screen_width * self.zoom) / 2
        self.y = target_y - (self.screen_height * self.zoom) / 2
    
    def world_to_screen(self, world_x: float, world_y: float) -> Tuple[int, int]:
        """
        Convert world coordinates to screen coordinates.
        
        Args:
            world_x, world_y: Position in the game world
            
        Returns:
            Tuple of (screen_x, screen_y) pixel coordinates
            
        This is essential for rendering - pygame draws at screen coordinates,
        but game logic works in world coordinates.
        """
        # Subtract camera position (what part of world we're viewing)
        # Then divide by zoom (to scale world down to screen)
        screen_x = int((world_x - self.x) / self.zoom)
        screen_y = int((world_y - self.y) / self.zoom)
        return screen_x, screen_y
    
    def screen_to_world(self, screen_x: int, screen_y: int) -> Tuple[float, float]:
        """
        Convert screen coordinates to world coordinates.
        
        Args:
            screen_x, screen_y: Pixel coordinates on the display
            
        Returns:
            Tuple of (world_x, world_y) position in game world
            
        Used for mouse input - when player clicks, we need to know what
        world position they clicked on.
        """
        # Inverse of world_to_screen: multiply by zoom, add camera position
        world_x = screen_x * self.zoom + self.x
        world_y = screen_y * self.zoom + self.y
        return world_x, world_y
    
    def is_visible(self, world_x: float, world_y: float, margin: int = 100) -> bool:
        """
        Check if a world position is visible on screen (with optional margin).
        
        Args:
            world_x, world_y: Position to check
            margin: Extra pixels outside screen to consider visible (for culling)
            
        Returns:
            True if the position would appear on screen
            
        OPTIMIZATION: This is used for view frustum culling - don't render
        objects that are off-screen. The margin prevents pop-in at edges.
        """
        screen_x, screen_y = self.world_to_screen(world_x, world_y)
        return (-margin <= screen_x <= self.screen_width + margin and 
                -margin <= screen_y <= self.screen_height + margin)

# =============================================================================
# GAME ENGINE CLASS
# =============================================================================
# This is the main game engine implementation. It manages:
#   - All game state (players, projectiles, game phase)
#   - Physics simulation (movement, collisions)
#   - Input handling and validation
#   - Rendering pipeline
#   - Synchronization with authoritative server
# =============================================================================

class GameEngine(IGameEngine):
    """
    Manages the entire game state, logic, input handling, and rendering.
    
    DESIGN PATTERN: This is a "thick client" - it maintains local state
    and renders immediately, but all authoritative decisions come from
    the server through process_report().
    
    STATE SYNCHRONIZATION:
        1. Client sends input actions to server
        2. Server validates and includes them in batch report
        3. Client receives report and applies changes
        4. Client renders the updated state
        
    This ensures all clients see the same game even with network lag.
    """
    
    def __init__(self, player_name: str, client_public_key: str):
        """
        Initialize the game engine with player identity.
        
        Args:
            player_name: Display name for this player
            client_public_key: Cryptographic public key identifying this client
            
        The public key serves as a unique player ID and enables
        cryptographic verification of actions (prevents impersonation).
        """
        # =====================================================================
        # PLAYER IDENTITY
        # =====================================================================
        self.player_name = player_name              # Human-readable name
        self.client_public_key = client_public_key  # Unique identifier

        # =====================================================================
        # CORE GAME STATE
        # =====================================================================
        # Game can be in three phases:
        #   WAITING: Lobby state, waiting for game to start
        #   ACTIVE: Game in progress
        #   GAME_OVER: Match concluded, showing winner
        self.GAME_STATE = "WAITING"
        
        # Players dictionary maps public_key -> player state
        # Each player has: position (x, y), color, health, last_shot_time
        self.players = {}
        
        # Projectiles list contains active bullets
        # Each projectile has: position, velocity, owner_id, creation_time
        self.projectiles = []
        
        # Winner tracking for game over screen
        self.winner_id = None

        # =====================================================================
        # LOCAL INPUT STATE
        # =====================================================================
        # Track which movement keys are currently held down
        # This enables continuous movement while key is pressed
        self.keys_down = {"w": False, "a": False, "s": False, "d": False}
        
        # Client-side cooldown enforcement prevents spamming the server
        # Server also validates cooldown (never trust the client!)
        self.local_last_shot_time = 0

        # =====================================================================
        # PYGAME INITIALIZATION
        # =====================================================================
        pygame.init()  # Initialize all pygame modules
        
        # Create the game window
        self.screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
        pygame.display.set_caption(f"Multiplayer Game Client: {self.player_name} (Press ESC to quit)")
        
        # Load fonts for UI text
        self.font = pygame.font.SysFont(None, 50)        # Large font for titles
        self.small_font = pygame.font.SysFont(None, 24)  # Small font for HUD
        
        # =====================================================================
        # CAMERA SYSTEM
        # =====================================================================
        # Initialize camera that will follow the local player
        self.camera = Camera(SCREEN_WIDTH, SCREEN_HEIGHT)

    def shutdown(self) -> None:
        """
        Cleanly shuts down the Pygame instance.
        
        This is important for proper resource cleanup. Without this,
        pygame windows might not close properly or could leave
        resources locked.
        """
        if pygame.get_init():  # Check if pygame is initialized
            pygame.quit()
            print("Pygame shut down cleanly.")

    # =========================================================================
    # CORE UPDATE LOGIC (THE GAME TICK)
    # =========================================================================
    # This section handles processing authoritative game state updates from
    # the server. This is where the "real" game state changes happen.
    # =========================================================================
    
    def process_report(self, report_params: BatchReportParams, action_queue: asyncio.Queue) -> None:
        """
        The main game tick. Processes the authoritative batch report and updates game state.
        
        Args:
            report_params: Contains delta timing and all events that occurred
            action_queue: Queue for sending responses (like auto-join on spawn)
            
        PROCESSING ORDER:
            1. Convert nanosecond timing to seconds for physics
            2. Update simulation (if game active)
            3. Process all events from the server
            4. Check win condition (if game active)
            5. Update camera to follow player
            
        This method is called whenever the server sends a batch report,
        which happens at a fixed tick rate (e.g., 60Hz on the server).
        """
        # Convert nanosecond timing to seconds for human-readable units
        # Server uses nanoseconds for precision, we use seconds for simplicity
        delta_seconds = report_params.deltaTiming / 1_000_000_000.0

        # Only simulate physics when game is active (not in lobby or game over)
        if self.GAME_STATE == "ACTIVE":
            self._update_simulation(delta_seconds)

        # Process all player actions included in this report
        self._process_events(report_params.deltaEvents, delta_seconds, action_queue)

        # Check if someone won (only matters during active gameplay)
        if self.GAME_STATE == "ACTIVE":
            self._check_win_condition()
        
        # Update camera to follow local player
        self._update_camera()

    def _wrap_position(self, x: float, y: float) -> Tuple[float, float]:
        """
        Wraps a position to stay within the toroidal world boundaries.
        
        Args:
            x, y: World coordinates (may be outside bounds)
            
        Returns:
            Wrapped coordinates (guaranteed to be within 0 to WORLD_WIDTH/HEIGHT)
            
        TOROIDAL TOPOLOGY:
            A toroidal world means the edges wrap around (like Pac-Man).
            Going off the right edge brings you back on the left.
            This creates an infinite-feeling world without actual infinite space.
            
        The modulo operator (%) handles wrapping elegantly:
            - If x = 3050 and WORLD_WIDTH = 3000, then x % 3000 = 50
            - If x = -50, then x % 3000 = 2950 (wraps to right side)
        """
        new_x = x % WORLD_WIDTH
        new_y = y % WORLD_HEIGHT
        return new_x, new_y

    def _update_simulation(self, delta_seconds: float):
        """
        Handles projectile movement, collision detection, time-based despawning, and damage.
        
        Args:
            delta_seconds: Time elapsed since last update (frame-rate independence)
            
        PHYSICS PIPELINE:
            1. Check each projectile for lifetime expiration
            2. Move projectile based on velocity and delta time
            3. Wrap projectile position (toroidal world)
            4. Check collision with all players
            5. Mark projectile for removal if it hit something or expired
            6. Apply accumulated damage to players
            7. Remove dead projectiles from the list
            
        FRAME-RATE INDEPENDENCE:
            By multiplying velocity by delta_seconds, the same distance is
            covered regardless of frame rate. At 60 FPS with delta=0.0167s
            and at 30 FPS with delta=0.033s, the object moves the same
            distance per real-world second.
        """
        # Track which projectiles should be removed (by ID)
        projectiles_to_remove = set()
        
        # Accumulate damage per player (multiple hits in one frame add up)
        damage_map = {}
        
        # Cache current time to avoid repeated system calls
        current_time = time.time()

        # Iterate through all active projectiles
        for proj in self.projectiles:
            # =================================================================
            # 1. CHECK FOR LIFETIME EXPIRATION
            # =================================================================
            # Projectiles don't live forever - they despawn after a set time
            # This prevents the world from filling up with missed shots
            if current_time - proj.get('creation_time', 0) > PROJECTILE_LIFETIME_SECONDS:
                projectiles_to_remove.add(proj['id'])
                continue  # Skip collision check for expired projectiles

            # =================================================================
            # 2. MOVE PROJECTILE (FRAME-RATE INDEPENDENT PHYSICS)
            # =================================================================
            # Update position based on velocity and elapsed time
            # velocity (units/sec) * time (sec) = displacement (units)
            proj['x'] += proj['vx'] * delta_seconds
            proj['y'] += proj['vy'] * delta_seconds
            
            # Wrap position to keep projectile within world bounds
            proj['x'], proj['y'] = self._wrap_position(proj['x'], proj['y'])

            # =================================================================
            # 3. CHECK FOR COLLISIONS WITH PLAYERS
            # =================================================================
            # Test against every player (O(n*m) where n=projectiles, m=players)
            # For large games, spatial partitioning would be more efficient
            for p_id, p_data in self.players.items():
                # Only collide with alive players who didn't shoot this projectile
                if p_data['health'] > 0 and p_id != proj['owner_id']:
                    # Create a rectangle for the player hitbox
                    player_rect = pygame.Rect(p_data['x'], p_data['y'], PLAYER_SIZE, PLAYER_SIZE)
                    
                    # Point-in-rectangle collision test
                    if player_rect.collidepoint(proj['x'], proj['y']):
                        # Hit! Mark projectile for removal and record damage
                        projectiles_to_remove.add(proj['id'])
                        damage_map[p_id] = damage_map.get(p_id, 0) + 1
                        break  # Projectile can only hit one player

        # =====================================================================
        # 4. APPLY ALL ACCUMULATED DAMAGE
        # =====================================================================
        # We batch damage application to avoid modifying player state
        # during collision detection (cleaner and more predictable)
        for p_id, damage in damage_map.items():
            if p_id in self.players:
                # Subtract damage but don't go below 0
                self.players[p_id]['health'] = max(0, self.players[p_id]['health'] - damage)

        # =====================================================================
        # 5. REMOVE DEAD PROJECTILES
        # =====================================================================
        # Rebuild list without the projectiles marked for removal
        # List comprehension is more efficient than removing items in-place
        self.projectiles = [p for p in self.projectiles if p['id'] not in projectiles_to_remove]

    def _process_events(self, events: List[Dict[str, Any]], delta_seconds: float, action_queue: asyncio.Queue):
        """
        Interprets the actions included in the report and applies them to the state.
        
        Args:
            events: List of signed actions from various players
            delta_seconds: Time delta for movement calculations
            action_queue: Queue for auto-responses (like auto-join)
            
        EVENT PROCESSING:
            Each event is a signed wrapper containing:
                - public_key: Who performed this action
                - payload: The actual action (method + params)
                - signature: Cryptographic proof (verified by server)
                
        The server has already validated all events. The client trusts
        the server and applies events in the order received.
        
        SUPPORTED EVENTS:
            - game.start: Transition from WAITING to ACTIVE
            - player.join: Add a new player to the game
            - player.move: Move a player in a direction
            - player.shoot: Fire a projectile toward a target
        """
        # Process each event in sequence
        for event_wrapper_dict in events:
            try:
                # =============================================================
                # PARSE AND VALIDATE EVENT STRUCTURE
                # =============================================================
                # Pydantic models ensure events match expected schema
                event_wrapper = SignedWrapper.model_validate(event_wrapper_dict)
                player_id = event_wrapper.public_key  # Who did this action
                request = JsonRpcRequest.model_validate(event_wrapper.payload)

                # =============================================================
                # HANDLE GAME START EVENT
                # =============================================================
                if request.method == "game.start":
                    # Transition from lobby to active gameplay
                    if self.GAME_STATE == "WAITING":
                        self.GAME_STATE = "ACTIVE"
                    continue  # No further processing needed

                # =============================================================
                # HANDLE PLAYER JOIN/SPAWN
                # =============================================================
                # Both "join" and "move" can trigger player creation
                # (move from a new player implies they want to join)
                if request.method in ["player.join", "player.move"]:
                    if player_id not in self.players:
                        # Add new player to the game
                        self._add_new_player(player_id)
                        
                        # If this is US joining, send a join confirmation
                        # This ensures server knows we're ready
                        asyncio.create_task(action_queue.put({"type": "join"}))

                # =============================================================
                # HANDLE GAMEPLAY ACTIONS (MOVE/SHOOT)
                # =============================================================
                # Only process actions for alive players during active game
                if self.GAME_STATE == "ACTIVE" and player_id in self.players and self.players[player_id]['health'] > 0:
                    if request.method == "player.move":
                        # Apply movement in the specified direction
                        self._move_player(player_id, request.params.direction, delta_seconds)
                    elif request.method == "player.shoot":
                        # Create projectile toward target coordinates
                        self._handle_shot(player_id, request.params.target_x, request.params.target_y)

            except (ValidationError, KeyError):
                # Skip malformed events (shouldn't happen with trusted server)
                # In production, you might log these for debugging
                continue

    def _add_new_player(self, player_id: str):
        """
        Initializes a new player with deterministic properties.
        
        Args:
            player_id: Unique identifier (public key) for the player
            
        DETERMINISTIC RANDOMNESS:
            We use the player_id as a random seed to generate properties.
            This ensures every client generates the same color and spawn
            position for a given player_id, maintaining visual consistency
            across all clients without explicit synchronization.
            
        After generating deterministic properties, we reset the random
        seed to avoid affecting other random operations.
        """
        # Seed the random generator with player_id for deterministic results
        random.seed(player_id)
        
        # Generate player properties
        self.players[player_id] = {
            # Random spawn position anywhere in the world
            "x": random.randint(0, WORLD_WIDTH),
            "y": random.randint(0, WORLD_HEIGHT),
            
            # Random color (avoiding too-dark colors with minimum 50)
            "color": (
                random.randint(50, 255),
                random.randint(50, 255),
                random.randint(50, 255)
            ),
            
            # Game state
            "health": MAX_HEALTH,         # Full health on spawn
            "last_shot_time": 0.0         # Can shoot immediately
        }
        
        # Reset random seed to avoid affecting other random calls
        random.seed()

    def _move_player(self, player_id: str, direction: str, delta_seconds: float):
        """
        Applies authoritative movement based on the network report.
        
        Args:
            player_id: Which player to move
            direction: 'w', 'a', 's', or 'd' (up, left, down, right)
            delta_seconds: Time elapsed (for frame-rate independence)
            
        MOVEMENT SYSTEM:
            - Speed is constant (PLAYER_SPEED_PER_SECOND)
            - Direction is axis-aligned (no diagonal movement in this version)
            - Position wraps at world boundaries (toroidal topology)
            
        FRAME-RATE INDEPENDENCE:
            Movement distance = speed * time
            This ensures smooth movement regardless of frame rate.
        """
        # Calculate how far to move this frame
        move_distance = PLAYER_SPEED_PER_SECOND * delta_seconds
        
        # Get player state reference
        player = self.players[player_id]
        
        # Apply movement based on direction
        # Note: Y increases downward in pygame (screen coordinates)
        if direction == "w":   # Up
            player["y"] -= move_distance
        elif direction == "s": # Down
            player["y"] += move_distance
        elif direction == "a": # Left
            player["x"] -= move_distance
        elif direction == "d": # Right
            player["x"] += move_distance
        
        # Wrap position to keep player within world bounds
        player["x"], player["y"] = self._wrap_position(player["x"], player["y"])

    def _handle_shot(self, player_id: str, target_x: int, target_y: int):
        """
        Validates a shot action and creates a projectile if valid.
        
        Args:
            player_id: Who fired the shot
            target_x, target_y: World coordinates where player aimed
            
        SHOOTING MECHANICS:
            1. Check cooldown (prevent spam)
            2. Calculate projectile direction
            3. Handle toroidal wrapping (shoot at nearest target image)
            4. Create projectile with constant speed
            
        TOROIDAL TARGETING:
            In a wrap-around world, there are multiple "images" of the
            target (direct, wrapped left/right, wrapped up/down).
            We shoot toward the NEAREST image to make aiming intuitive.
        """
        current_time = time.time()
        player_state = self.players[player_id]
        
        # =================================================================
        # COOLDOWN CHECK
        # =================================================================
        # Enforce minimum time between shots (game balance rule)
        if (current_time - player_state.get('last_shot_time', 0)) > SHOOT_COOLDOWN:
            # Update last shot time
            player_state['last_shot_time'] = current_time

            # =============================================================
            # CALCULATE STARTING POSITION (CENTER OF PLAYER)
            # =============================================================
            start_x = player_state['x'] + PLAYER_SIZE / 2
            start_y = player_state['y'] + PLAYER_SIZE / 2

            # =============================================================
            # TOROIDAL DISTANCE CALCULATION
            # =============================================================
            # Find the shortest path to target considering wrap-around
            # For each axis, there are 3 possibilities:
            #   1. Direct path
            #   2. Path wrapping around right/bottom edge
            #   3. Path wrapping around left/top edge
            
            dx_options = [
                target_x - start_x,                      # Direct
                (target_x + WORLD_WIDTH) - start_x,      # Wrapped right
                (target_x - WORLD_WIDTH) - start_x       # Wrapped left
            ]
            dy_options = [
                target_y - start_y,                      # Direct
                (target_y + WORLD_HEIGHT) - start_y,     # Wrapped down
                (target_y - WORLD_HEIGHT) - start_y      # Wrapped up
            ]
            
            # Choose the option with smallest absolute distance
            dir_x = min(dx_options, key=abs)
            dir_y = min(dy_options, key=abs)

            # =============================================================
            # NORMALIZE DIRECTION AND CREATE PROJECTILE
            # =============================================================
            # Convert to unit vector, then scale to desired speed
            length = sqrt(dir_x**2 + dir_y**2)
            
            # Avoid division by zero if target is exactly at player position
            if length > 0:
                # Calculate velocity components (constant speed)
                vx = (dir_x / length) * PROJECTILE_SPEED_PER_SECOND
                vy = (dir_y / length) * PROJECTILE_SPEED_PER_SECOND
                
                # Create new projectile
                self.projectiles.append({
                    "x": start_x,
                    "y": start_y,
                    "vx": vx,
                    "vy": vy,
                    "owner_id": player_id,  # Can't hit yourself
                    "id": str(uuid.uuid4()),  # Unique identifier
                    "creation_time": time.time()  # For lifetime tracking
                })

    def _check_win_condition(self):
        """
        Checks if the game has ended and determines the winner.
        
        WIN CONDITION:
            The game ends when only 0 or 1 players remain alive.
            If exactly 1 player survives, they are the winner.
            If 0 players survive (mutual destruction), there's no winner.
            
        EDGE CASE:
            We check len(self.players) > 0 to avoid ending an empty lobby.
        """
        # Count players with health remaining
        active_players = [pid for pid, pdata in self.players.items() if pdata['health'] > 0]
        
        # Game ends when 1 or fewer players remain (and game has started)
        if len(active_players) <= 1 and len(self.players) > 0:
            self.GAME_STATE = "GAME_OVER"
            # Winner is the last survivor (or None if everyone died)
            self.winner_id = active_players[0] if active_players else None
    
    def _update_camera(self):
        """
        Update camera to follow the local player.
        
        This creates a smooth "follow cam" effect where the player
        stays centered on screen. The camera only updates if the local
        player exists (they might not have joined yet).
        """
        if self.client_public_key in self.players:
            player = self.players[self.client_public_key]
            # Center on player's center point (not top-left corner)
            target_x = player['x'] + PLAYER_SIZE / 2
            target_y = player['y'] + PLAYER_SIZE / 2
            self.camera.update(target_x, target_y)

    # =========================================================================
    # INPUT HANDLING
    # =========================================================================
    # This section manages user input (keyboard and mouse) and converts it
    # into game actions. Actions are queued for network transmission to the
    # server, NOT applied locally (prevents cheating).
    # =========================================================================
    
    def handle_input(self, action_queue: asyncio.Queue) -> bool:
        """
        Handles Pygame events and adds corresponding actions to the queue.
        
        Args:
            action_queue: Queue where actions are placed for network transmission
            
        Returns:
            False if user wants to quit, True otherwise
            
        INPUT PHILOSOPHY:
            We don't apply input locally - we send it to the server for
            validation, then apply it when we receive the authoritative
            report. This prevents cheating and ensures all clients agree.
            
        CONTINUOUS INPUT:
            Movement keys can be held down for continuous movement.
            We track key states and send move actions every frame while held.
        """
        # Check if local player is alive and can perform actions
        our_player_alive = (self.client_public_key in self.players and 
                           self.players[self.client_public_key]['health'] > 0)
        running = True

        # =================================================================
        # PROCESS PYGAME EVENTS
        # =================================================================
        for event in pygame.event.get():
            # =============================================================
            # QUIT EVENTS (Window close or ESC key)
            # =============================================================
            if event.type == pygame.QUIT or (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE):
                running = False
                return running

            # =============================================================
            # LOBBY: SPACE TO START GAME
            # =============================================================
            if event.type == pygame.KEYDOWN and event.key == pygame.K_SPACE and self.GAME_STATE == "WAITING":
                # Send game start action to server
                asyncio.create_task(action_queue.put({"type": "start"}))

            # =============================================================
            # GAMEPLAY: ACTIONS AVAILABLE TO ALIVE PLAYERS
            # =============================================================
            if self.GAME_STATE == "ACTIVE" and our_player_alive:
                # ==========================================================
                # MOUSE CLICK: SHOOT PROJECTILE
                # ==========================================================
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:  # Left click
                    self._process_local_shot(event.pos, action_queue)

                # ==========================================================
                # MOVEMENT: TRACK KEY DOWN EVENTS
                # ==========================================================
                if event.type == pygame.KEYDOWN:
                    key_name = pygame.key.name(event.key)
                    if key_name in self.keys_down:
                        self.keys_down[key_name] = True
                
                # ==========================================================
                # MOVEMENT: TRACK KEY UP EVENTS
                # ==========================================================
                elif event.type == pygame.KEYUP:
                    key_name = pygame.key.name(event.key)
                    if key_name in self.keys_down:
                        self.keys_down[key_name] = False

        # =================================================================
        # CONTINUOUS MOVEMENT
        # =================================================================
        # Send move actions for any keys currently held down
        if self.GAME_STATE == "ACTIVE" and our_player_alive:
            self._process_continuous_movement(action_queue)

        return running

    def _process_local_shot(self, pos, action_queue: asyncio.Queue):
        """
        Checks the local cooldown before sending a shot action.
        
        Args:
            pos: Mouse position in screen coordinates
            action_queue: Queue for sending action to server
            
        CLIENT-SIDE COOLDOWN:
            We enforce cooldown locally to provide immediate feedback
            (no delay waiting for server round-trip). However, the server
            ALSO validates cooldown - never trust the client!
            
        COORDINATE TRANSFORMATION:
            Mouse gives screen coordinates, but we need world coordinates
            for the target position. Camera handles this conversion.
        """
        current_time = time.time()
        
        # Only send shot if enough time has passed (local validation)
        if (current_time - self.local_last_shot_time) > SHOOT_COOLDOWN:
            self.local_last_shot_time = current_time
            
            # Convert mouse position from screen to world coordinates
            world_x, world_y = self.camera.screen_to_world(pos[0], pos[1])
            
            # Queue shoot action for network transmission
            asyncio.create_task(action_queue.put({
                "type": "shoot",
                "target": (int(world_x), int(world_y))
            }))

    def _process_continuous_movement(self, action_queue: asyncio.Queue):
        """
        Sends movement actions for keys currently held down.
        
        Args:
            action_queue: Queue for network transmission
            
        This is called every frame during input handling. Any keys marked
        as pressed will generate move actions. The server receives these
        at a high rate and applies them to create smooth movement.
        """
        for key, pressed in self.keys_down.items():
            if pressed:
                # Queue move action for this direction
                asyncio.create_task(action_queue.put({
                    "type": "move",
                    "dir": key
                }))

    # =========================================================================
    # RENDERING
    # =========================================================================
    # This section handles drawing the game state to the screen.
    # The rendering pipeline runs independently of game logic (can have
    # different frame rates).
    # =========================================================================
    
    def render(self) -> None:
        """
        Renders the current game state to the screen.
        
        RENDERING PIPELINE:
            1. Clear screen with background color
            2. Render toroidal world (9 tiles for wrap-around effect)
            3. Render UI overlays (lobby text, game over, HUD)
            4. Update display buffer (flip)
            
        The render order matters: background first, then game objects,
        then UI on top.
        """
        # Clear screen with dark background
        self.screen.fill((25, 25, 35))
        
        # Render the game world with wrap-around
        self._render_toroidal_world()

        # Render UI based on game state
        if self.GAME_STATE == "WAITING":
            self._render_lobby_text()
        elif self.GAME_STATE == "GAME_OVER":
            self._render_game_over()

        # Always render HUD elements
        self._draw_position_indicator()
        self._draw_edge_indicators()

        # Swap display buffers to show rendered frame
        pygame.display.flip()

    def _render_toroidal_world(self):
        """
        Renders the game world 9 times to create a seamless toroidal (wrap-around) effect.
        
        TOROIDAL RENDERING TECHNIQUE:
            A toroidal world wraps at the edges. To show this visually,
            we render the world 9 times in a 3x3 grid:
            
                [TL] [T ] [TR]
                [L ] [C ] [R ]
                [BL] [B ] [BR]
                
            Where C is the actual world, and the 8 surrounding tiles are
            copies offset by WORLD_WIDTH/HEIGHT. This creates seamless
            wrap-around where objects moving off one edge appear on the other.
            
        WHY 9 TILES?
            With the camera view potentially spanning edges, we need to
            show objects that have wrapped. Rendering 9 tiles ensures
            everything visible on screen is drawn correctly.
            
        OPTIMIZATION:
            This is expensive (9x rendering cost). For larger games,
            you'd only render visible tiles or use shader tricks.
        """
        # Save original camera position
        original_cam_x, original_cam_y = self.camera.x, self.camera.y
        
        # Render 9 tiles (3x3 grid)
        for i in range(-1, 2):      # -1, 0, 1 (left, center, right)
            for j in range(-1, 2):  # -1, 0, 1 (top, center, bottom)
                # Calculate world offset for this tile
                offset_x = i * WORLD_WIDTH
                offset_y = j * WORLD_HEIGHT
                
                # Adjust camera to render this tile
                # We subtract the offset to shift the world view
                self.camera.x = original_cam_x - offset_x
                self.camera.y = original_cam_y - offset_y
                
                # Render all game objects at this tile position
                self._render_all_game_objects()

        # Restore original camera position for UI rendering
        self.camera.x, self.camera.y = original_cam_x, original_cam_y
        
    def _draw_toroidal_grid(self):
        """
        Draws grid lines for the entire world, called by the 9-tile renderer.
        
        The grid helps players orient themselves in the world and makes
        movement more apparent. Grid lines are drawn behind all game objects.
        """
        grid_size = 100  # Space between grid lines
        grid_color = (40, 40, 50)  # Subtle gray

        # =================================================================
        # DRAW VERTICAL LINES
        # =================================================================
        for x in range(0, WORLD_WIDTH + 1, grid_size):
            # Convert world positions to screen coordinates
            start_pos = self.camera.world_to_screen(x, 0)
            end_pos = self.camera.world_to_screen(x, WORLD_HEIGHT)
            pygame.draw.line(self.screen, grid_color, start_pos, end_pos)
        
        # =================================================================
        # DRAW HORIZONTAL LINES
        # =================================================================
        for y in range(0, WORLD_HEIGHT + 1, grid_size):
            start_pos = self.camera.world_to_screen(0, y)
            end_pos = self.camera.world_to_screen(WORLD_WIDTH, y)
            pygame.draw.line(self.screen, grid_color, start_pos, end_pos)

    def _render_all_game_objects(self):
        """
        Renders the grid, players, and projectiles.
        Called multiple times by _render_toroidal_world.
        
        RENDER ORDER:
            1. Grid (background)
            2. Players (with health bars)
            3. Projectiles (on top)
            
        SCALING:
            All sizes are divided by camera.zoom to make objects appear
            correct size at the current zoom level.
        """
        # Draw background grid first
        self._draw_toroidal_grid()

        # =================================================================
        # CALCULATE SCALED SIZES
        # =================================================================
        # Divide by zoom so objects maintain visual size at different zooms
        scaled_player_size = PLAYER_SIZE / self.camera.zoom
        scaled_health_bar_height = 10 / self.camera.zoom
        scaled_health_bar_offset = 15 / self.camera.zoom

        # =================================================================
        # RENDER PLAYERS
        # =================================================================
        for p_data in self.players.values():
            # Only render alive players that are visible
            if p_data['health'] > 0 and self.camera.is_visible(p_data['x'], p_data['y']):
                # Convert world position to screen position
                screen_x, screen_y = self.camera.world_to_screen(p_data['x'], p_data['y'])
                
                # ==========================================================
                # DRAW HEALTH BAR (ONLY DURING GAMEPLAY)
                # ==========================================================
                if self.GAME_STATE == "ACTIVE":
                    # Background (red bar showing max health)
                    pygame.draw.rect(self.screen, (100, 0, 0), 
                                   pygame.Rect(screen_x, 
                                             screen_y - scaled_health_bar_offset, 
                                             scaled_player_size, 
                                             scaled_health_bar_height))
                    
                    # Foreground (green bar showing current health)
                    health_width = (p_data['health'] / MAX_HEALTH) * scaled_player_size
                    pygame.draw.rect(self.screen, (0, 200, 0), 
                                   pygame.Rect(screen_x, 
                                             screen_y - scaled_health_bar_offset, 
                                             health_width, 
                                             scaled_health_bar_height))
                
                # ==========================================================
                # DRAW PLAYER SQUARE
                # ==========================================================
                pygame.draw.rect(self.screen, p_data["color"], 
                               pygame.Rect(screen_x, screen_y, 
                                         scaled_player_size, 
                                         scaled_player_size))

        # =================================================================
        # RENDER PROJECTILES
        # =================================================================
        scaled_projectile_size = max(2, PROJECTILE_SIZE / self.camera.zoom)
        
        for proj in self.projectiles:
            # Only render visible projectiles
            if self.camera.is_visible(proj['x'], proj['y']):
                screen_x, screen_y = self.camera.world_to_screen(proj['x'], proj['y'])
                # Draw as yellow circle
                pygame.draw.circle(self.screen, 
                                 (255, 255, 100),  # Yellow
                                 (int(screen_x), int(screen_y)), 
                                 int(scaled_projectile_size))

    def _render_lobby_text(self):
        """
        Renders the lobby screen text showing player count and start instruction.
        
        This is shown during the WAITING state before the game begins.
        Text is centered on the screen for visual appeal.
        """
        # Player count
        count_text = self.font.render(f"{len(self.players)} Players Connected", True, (255, 255, 255))
        self.screen.blit(count_text, 
                        (SCREEN_WIDTH/2 - count_text.get_width()/2, 40))
        
        # Start instruction
        start_text = self.font.render("Press SPACE to Start", True, (200, 200, 200))
        self.screen.blit(start_text, 
                        (SCREEN_WIDTH/2 - start_text.get_width()/2, 90))

    def _draw_position_indicator(self):
        """
        Draw player position coordinates in the corner.
        
        This helps with debugging and gives players awareness of their
        location in the world. Useful in a large toroidal world where
        it's easy to get disoriented.
        """
        if self.client_public_key in self.players:
            player = self.players[self.client_public_key]
            # Format as "Position: (x, y)"
            pos_text = self.small_font.render(
                f"Position: ({int(player['x'])}, {int(player['y'])})", 
                True, (200, 200, 200)
            )
            # Draw in top-left corner
            self.screen.blit(pos_text, (10, 10))
    
    def _draw_edge_indicators(self):
        """
        Draw triangular indicators at screen edges pointing toward off-screen players.
        
        PROBLEM: In a large world, other players might be off-screen
        SOLUTION: Show arrows at screen edges pointing toward hidden players
        
        This helps players maintain situational awareness and find opponents.
        
        ALGORITHM:
            1. For each other player
            2. Calculate toroidal direction to that player
            3. Check if they're off-screen
            4. If yes, calculate where edge indicator should appear
            5. Draw triangle pointing toward the player
        """
        # Can't show indicators if we don't exist yet
        if self.client_public_key not in self.players:
            return
        
        # Get our position (center of player square)
        local_player = self.players[self.client_public_key]
        local_x = local_player['x'] + PLAYER_SIZE / 2
        local_y = local_player['y'] + PLAYER_SIZE / 2
        
        # =================================================================
        # CHECK EACH OTHER PLAYER
        # =================================================================
        for player_id, player_data in self.players.items():
            # Skip self and dead players
            if player_id == self.client_public_key or player_data['health'] <= 0:
                continue
            
            # Get target player position
            player_x = player_data['x'] + PLAYER_SIZE / 2
            player_y = player_data['y'] + PLAYER_SIZE / 2
            
            # =============================================================
            # CALCULATE TOROIDAL DIRECTION
            # =============================================================
            # Find shortest path considering wrap-around
            dx_options = [
                player_x - local_x,
                (player_x + WORLD_WIDTH) - local_x,
                (player_x - WORLD_WIDTH) - local_x
            ]
            dy_options = [
                player_y - local_y,
                (player_y + WORLD_HEIGHT) - local_y,
                (player_y - WORLD_HEIGHT) - local_y
            ]
            dx = min(dx_options, key=abs)
            dy = min(dy_options, key=abs)

            # Calculate actual target position (closest image)
            closest_target_x = local_x + dx
            closest_target_y = local_y + dy
            
            # =============================================================
            # CHECK IF TARGET IS ON-SCREEN
            # =============================================================
            # Use negative margin to only show indicator when clearly off-screen
            if self.camera.is_visible(closest_target_x, closest_target_y, margin=-50):
                continue  # Player is visible, no indicator needed

            # =============================================================
            # NORMALIZE DIRECTION VECTOR
            # =============================================================
            distance = sqrt(dx * dx + dy * dy)
            if distance < 1:
                continue  # Too close to determine direction
            
            # Convert to unit vector
            dx /= distance
            dy /= distance
            
            # =============================================================
            # CALCULATE EDGE INTERSECTION
            # =============================================================
            # Find where a ray from screen center in this direction hits the edge
            screen_center_x = SCREEN_WIDTH / 2
            screen_center_y = SCREEN_HEIGHT / 2
            
            edge_x, edge_y = self._calculate_edge_intersection(
                screen_center_x, screen_center_y, dx, dy
            )
            
            # =============================================================
            # DRAW INDICATOR
            # =============================================================
            self._draw_triangle_indicator(edge_x, edge_y, dx, dy, player_data['color'])
    
    def _calculate_edge_intersection(self, center_x: float, center_y: float, 
                                     dir_x: float, dir_y: float) -> Tuple[float, float]:
        """
        Calculate where a ray from center intersects the screen edge.
        
        Args:
            center_x, center_y: Starting point (usually screen center)
            dir_x, dir_y: Direction vector (should be normalized)
            
        Returns:
            Tuple of (x, y) where ray hits screen edge
            
        ALGORITHM:
            We have a parametric ray: point(t) = center + t * direction
            We want to find t where the ray intersects each screen edge.
            Then we pick the first intersection (smallest positive t).
            
        EDGE EQUATIONS:
            Left edge:   x = margin
            Right edge:  x = SCREEN_WIDTH - margin
            Top edge:    y = margin
            Bottom edge: y = SCREEN_HEIGHT - margin
            
        We solve for t in each case and check if the other coordinate
        is within bounds.
        """
        margin = 30  # Keep indicators away from exact edge
        intersections = []
        
        # =================================================================
        # CHECK LEFT EDGE
        # =================================================================
        if dir_x < -1e-6:  # Moving left (with epsilon for float comparison)
            t = (margin - center_x) / dir_x  # Solve for t
            y = center_y + t * dir_y          # Calculate y at intersection
            if margin <= y <= SCREEN_HEIGHT - margin:  # Check bounds
                intersections.append((margin, y))
        
        # =================================================================
        # CHECK RIGHT EDGE
        # =================================================================
        if dir_x > 1e-6:  # Moving right
            t = (SCREEN_WIDTH - margin - center_x) / dir_x
            y = center_y + t * dir_y
            if margin <= y <= SCREEN_HEIGHT - margin:
                intersections.append((SCREEN_WIDTH - margin, y))
        
        # =================================================================
        # CHECK TOP EDGE
        # =================================================================
        if dir_y < -1e-6:  # Moving up
            t = (margin - center_y) / dir_y
            x = center_x + t * dir_x
            if margin <= x <= SCREEN_WIDTH - margin:
                intersections.append((x, margin))
        
        # =================================================================
        # CHECK BOTTOM EDGE
        # =================================================================
        if dir_y > 1e-6:  # Moving down
            t = (SCREEN_HEIGHT - margin - center_y) / dir_y
            x = center_x + t * dir_x
            if margin <= x <= SCREEN_WIDTH - margin:
                intersections.append((x, SCREEN_HEIGHT - margin))
        
        # =================================================================
        # RETURN FIRST INTERSECTION (or corner if none found)
        # =================================================================
        if intersections:
            return intersections[0]
        
        # Fallback: return appropriate corner
        return (SCREEN_WIDTH - margin if dir_x > 0 else margin, 
                SCREEN_HEIGHT - margin if dir_y > 0 else margin)
    
    def _draw_triangle_indicator(self, x: float, y: float, 
                                 dir_x: float, dir_y: float, 
                                 color: Tuple[int, int, int]):
        """
        Draw a triangle pointing in the given direction.
        
        Args:
            x, y: Position on screen edge
            dir_x, dir_y: Direction vector (normalized)
            color: Color of the triangle (matches target player)
            
        TRIANGLE CONSTRUCTION:
            The triangle has a tip pointing toward the player and a base.
            We construct it by:
                1. Tip is at (x, y)
                2. Calculate angle from direction vector
                3. Base points are perpendicular to direction, behind tip
        """
        size = 15  # Triangle size
        
        # Calculate angle in radians
        angle = math.atan2(dir_y, dir_x)
        
        # Tip of triangle (pointing toward player)
        tip_x, tip_y = (x, y)
        
        # Calculate base angles (perpendicular to direction)
        base_angle1 = angle + math.pi / 2   # 90 degrees clockwise
        base_angle2 = angle - math.pi / 2   # 90 degrees counter-clockwise
        
        # Calculate base points (behind tip, on either side)
        base1_x = x - math.cos(angle) * size + math.cos(base_angle1) * size / 2
        base1_y = y - math.sin(angle) * size + math.sin(base_angle1) * size / 2
        
        base2_x = x - math.cos(angle) * size + math.cos(base_angle2) * size / 2
        base2_y = y - math.sin(angle) * size + math.sin(base_angle2) * size / 2
        
        # Draw filled triangle with player's color
        points = [(tip_x, tip_y), (base1_x, base1_y), (base2_x, base2_y)]
        pygame.draw.polygon(self.screen, color, points)
        
        # Draw white outline for contrast
        pygame.draw.polygon(self.screen, (255, 255, 255), points, 2)

    def _render_game_over(self):
        """
        Renders the game over screen and the winner.
        
        Shows a prominent "GAME OVER" message and identifies the winner
        (if there is one). Winner text is shown in their player color.
        """
        # Draw "GAME OVER" in red
        over_text = self.font.render("GAME OVER", True, (255, 0, 0))
        self.screen.blit(over_text, 
                        (SCREEN_WIDTH/2 - over_text.get_width()/2, 
                         SCREEN_HEIGHT/2 - 50))
        
        # If there's a winner, show their ID
        if self.winner_id and self.winner_id in self.players:
            # Extract a short snippet of the winner's ID for display
            # (full public keys are too long to show)
            winner_snippet = self.winner_id.splitlines()[-2][-10:]
            
            # Draw winner text in their color
            winner_text = self.font.render(
                f"Winner: {winner_snippet}", 
                True, 
                self.players[self.winner_id]['color']
            )
            self.screen.blit(winner_text, 
                           (SCREEN_WIDTH/2 - winner_text.get_width()/2, 
                            SCREEN_HEIGHT/2 + 10))