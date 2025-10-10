from crypto_utils import generate_key_pair
import os
import argparse

# ---- 1. Set up Command-Line Argument Parser ----
# This allows us to accept user input from the command line, in this case, a name for the key set.
parser = argparse.ArgumentParser(description="Generate a named key pair for a game client.")
# Add the '--name' argument. It's required, so the script won't run without it.
parser.add_argument('--name', type=str, required=True, help='A unique name for the key pair directory (e.g., alice, bob).')
args = parser.parse_args()

# ---- 2. Define and Create the Directory Path ----
# Construct the path for the new key directory, e.g., "keys/alice".
key_directory = os.path.join("keys", args.name)

# Create the directory if it doesn't already exist.
# The `exist_ok=True` flag prevents an error if the directory is already there.
os.makedirs(key_directory, exist_ok=True)

# ---- 3. Generate the Keys ----
# Define the full paths for the private and public key files.
private_key_path = os.path.join(key_directory, "client_private_key.pem")
public_key_path = os.path.join(key_directory, "client_public_key.pem")

print(f"Generating client key pair for '{args.name}'...")

# Call the function from crypto_utils to generate and save the keys to the specified paths.
generate_key_pair(
    private_key_path=private_key_path,
    public_key_path=public_key_path
)

# ---- 4. Provide Feedback to the User ----
print(f"\nKey generation complete. Keys saved in '{key_directory}/'")