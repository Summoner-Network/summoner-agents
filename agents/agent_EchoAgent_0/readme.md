# Describe the Agent Here

```
python server.py

python agents/agent_EchoAgent_0/agent.py

python agents/agent_EchoAgent_0/agent.py

python agents/agent_SendAgent_0/agent.py

```

Disconnect quickly
```
python agents/agent_SendAgent_0/agent.py
[DEBUG] Loaded config from: configs/client_config.json
2025-07-23 01:38:19.005 - SendAgent_0 - INFO - Connected to server @(host=127.0.0.1, port=8888)
^C2025-07-23 01:38:21.312 - SendAgent_0 - INFO - Client is shutting down...
2025-07-23 01:38:21.313 - SendAgent_0 - INFO - Client about to disconnect...
2025-07-23 01:38:21.315 - SendAgent_0 - INFO - Client exited cleanly.
```

```
2025-07-23 01:38:10.595 - EchoAgent_0 - INFO - Connected to server @(host=127.0.0.1, port=8888)
2025-07-23 01:38:20.009 - EchoAgent_0 - INFO - Buffered message from:(SocketAddress=127.0.0.1:58786).
2025-07-23 01:38:21.011 - EchoAgent_0 - INFO - Buffered message from:(SocketAddress=127.0.0.1:58786).
2025-07-23 01:38:21.013 - EchoAgent_0 - INFO - Buffered message from:(SocketAddress=127.0.0.1:58785).
2025-07-23 01:38:22.016 - EchoAgent_0 - INFO - Buffered message from:(SocketAddress=127.0.0.1:58785).
2025-07-23 01:38:23.016 - EchoAgent_0 - INFO - Buffered message from:(SocketAddress=127.0.0.1:58785).
2025-07-23 01:38:24.020 - EchoAgent_0 - INFO - Buffered message from:(SocketAddress=127.0.0.1:58785).
```

```
2025-07-23 01:38:14.611 - EchoAgent_0 - INFO - Connected to server @(host=127.0.0.1, port=8888)
2025-07-23 01:38:20.008 - EchoAgent_0 - INFO - Buffered message from:(SocketAddress=127.0.0.1:58786).
2025-07-23 01:38:21.011 - EchoAgent_0 - INFO - Buffered message from:(SocketAddress=127.0.0.1:58786).
2025-07-23 01:38:21.013 - EchoAgent_0 - INFO - Buffered message from:(SocketAddress=127.0.0.1:58784).
2025-07-23 01:38:22.015 - EchoAgent_0 - INFO - Buffered message from:(SocketAddress=127.0.0.1:58784).
2025-07-23 01:38:23.016 - EchoAgent_0 - INFO - Buffered message from:(SocketAddress=127.0.0.1:58784).
2025-07-23 01:38:24.019 - EchoAgent_0 - INFO - Buffered message from:(SocketAddress=127.0.0.1:58784).
2025-07-23 01:38:25.019 - EchoAgent_0 - INFO - Buffered message from:(SocketAddress=127.0.0.1:58784).
2025-07-23 01:38:26.021 - EchoAgent_0 - INFO - Buffered message from:(SocketAddress=127.0.0.1:58784).
```



``
python server.py

python agents/agent_EchoAgent_0/agent.py

python agents/agent_RecvAgent_0/agent.py

python agents/agent_SendAgent_0/agent.py

```

Disconnect quickly
```
python agents/agent_SendAgent_0/agent.py
[DEBUG] Loaded config from: configs/client_config.json
2025-07-23 01:38:19.005 - SendAgent_0 - INFO - Connected to server @(host=127.0.0.1, port=8888)
^C2025-07-23 01:38:21.312 - SendAgent_0 - INFO - Client is shutting down...
2025-07-23 01:38:21.313 - SendAgent_0 - INFO - Client about to disconnect...
2025-07-23 01:38:21.315 - SendAgent_0 - INFO - Client exited cleanly.
```

```
2025-07-23 01:40:16.343 - EchoAgent_0 - INFO - Connected to server @(host=127.0.0.1, port=8888)
2025-07-23 01:40:26.264 - EchoAgent_0 - INFO - Buffered message from:(SocketAddress=127.0.0.1:58831).
2025-07-23 01:40:27.267 - EchoAgent_0 - INFO - Buffered message from:(SocketAddress=127.0.0.1:58831).
2025-07-23 01:40:28.270 - EchoAgent_0 - INFO - Buffered message from:(SocketAddress=127.0.0.1:58831).
2025-07-23 01:40:29.271 - EchoAgent_0 - INFO - Buffered message from:(SocketAddress=127.0.0.1:58831).
2025-07-23 01:40:30.274 - EchoAgent_0 - INFO - Buffered message from:(SocketAddress=127.0.0.1:58831).
```

Echo has a delay:

```
2025-07-23 01:40:20.329 - RecvAgent_0 - INFO - Connected to server @(host=127.0.0.1, port=8888)
2025-07-23 01:40:26.264 - RecvAgent_0 - INFO - Received message from Client @(SocketAddress=127.0.0.1:58831).
2025-07-23 01:40:26.267 - RecvAgent_0 - INFO - Client @(SocketAddress=127.0.0.1:58831) has now 1 messages stored.
2025-07-23 01:40:27.267 - RecvAgent_0 - INFO - Received message from Client @(SocketAddress=127.0.0.1:58831).
2025-07-23 01:40:27.274 - RecvAgent_0 - INFO - Client @(SocketAddress=127.0.0.1:58831) has now 2 messages stored.
2025-07-23 01:40:27.275 - RecvAgent_0 - INFO - Received message from Client @(SocketAddress=127.0.0.1:58828).
2025-07-23 01:40:27.277 - RecvAgent_0 - INFO - Client @(SocketAddress=127.0.0.1:58828) has now 1 messages stored.
2025-07-23 01:40:28.269 - RecvAgent_0 - INFO - Received message from Client @(SocketAddress=127.0.0.1:58831).
2025-07-23 01:40:28.273 - RecvAgent_0 - INFO - Client @(SocketAddress=127.0.0.1:58831) has now 3 messages stored.
2025-07-23 01:40:28.277 - RecvAgent_0 - INFO - Received message from Client @(SocketAddress=127.0.0.1:58828).
2025-07-23 01:40:28.279 - RecvAgent_0 - INFO - Client @(SocketAddress=127.0.0.1:58828) has now 2 messages stored.
```