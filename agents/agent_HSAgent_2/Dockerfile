# Dockerfile for HSAgent_2 (to be placed in the agent's directory)

# Use an official Python runtime as a parent image.
FROM python:3.11-slim

# Set the working directory for the agent inside the container.
WORKDIR /usr/src/app/agent

# --- Build Stage ---
# This stage copies ALL necessary source code and installs dependencies.

# Set the project root as the working directory temporarily.
WORKDIR /usr/src/app

# Copy all the agent source code and the shared configs.
# These paths are relative to the build context (the project root).
COPY . .

# Install git, which is required to clone the source code.
# We clean up the apt cache in the same layer to keep the image small.
RUN apt-get update && \
    apt-get install -y git && \
    rm -rf /var/lib/apt/lists/*

# --- Security: Create a Non-Root User ---
# Running as a non-root user is a critical security best practice.
# RUN addgroup --system app && adduser --system --group app

# --- Application Setup ---

# Clone the agent's source code from the canonical repository.
# The '.' at the end clones it into the current WORKDIR (/app).
RUN git clone https://github.com/Summoner-Network/summoner-core.git && \
    mv summoner-core/summoner . && \
    rm -rf summoner-core

# Install the dependencies.
RUN pip install --no-cache-dir -r ./requirements.txt

# Switch to our non-root user. All subsequent commands will run as this user.
# USER app

# Use ENTRYPOINT for the base command and arguments that will not change.
ENTRYPOINT ["python", "agent.py", "--config", "./configs/client_config.json"]

# Use CMD for the default, overridable arguments.
# These will be appended to the ENTRYPOINT.
CMD ["--name", "docker-agent-default"]

