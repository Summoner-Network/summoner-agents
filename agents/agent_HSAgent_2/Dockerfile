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

# Install the dependencies.
RUN pip install --no-cache-dir -r ./requirements.txt

# Use ENTRYPOINT for the base command and arguments that will not change.
ENTRYPOINT ["python", "agent.py", "--config", "../../configs/client_config.json"]

# Use CMD for the default, overridable arguments.
# These will be appended to the ENTRYPOINT.
CMD ["--name", "docker-agent-default"]

