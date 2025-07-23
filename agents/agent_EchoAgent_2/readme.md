# Describe the Agent Here

```
python server.py
```

```
python agents/agent_SendAgent_0/agent.py
```

```
python agents/agent_RecvAgent_2/agent.py
```

```
[DEBUG] Loaded config from: configs/client_config.json
2025-07-23 09:40:45.344 - RecvAgent_2 - INFO - Connected to server @(host=127.0.0.1, port=8888)
2025-07-23 09:40:45.509 - RecvAgent_2 - INFO - [hook:recv] 127.0.0.1:49577 -> Banned? False
2025-07-23 09:40:45.512 - RecvAgent_2 - INFO - [hook:recv] 127.0.0.1:49577 invalid -> checking if ban is required...
2025-07-23 09:40:46.512 - RecvAgent_2 - INFO - [hook:recv] 127.0.0.1:49577 -> Banned? False
2025-07-23 09:40:46.515 - RecvAgent_2 - INFO - [hook:recv] 127.0.0.1:49577 invalid -> checking if ban is required...
2025-07-23 09:40:47.511 - RecvAgent_2 - INFO - [hook:recv] 127.0.0.1:49577 -> Banned? False
2025-07-23 09:40:47.513 - RecvAgent_2 - INFO - [hook:recv] 127.0.0.1:49577 invalid -> checking if ban is required...

... (20 of these)

2025-07-23 09:41:03.539 - RecvAgent_2 - INFO - [hook:recv] 127.0.0.1:49577 invalid -> checking if ban is required...
2025-07-23 09:41:04.538 - RecvAgent_2 - INFO - [hook:recv] 127.0.0.1:49577 -> Banned? False
2025-07-23 09:41:04.545 - RecvAgent_2 - INFO - [hook:recv] 127.0.0.1:49577 invalid -> checking if ban is required...
2025-07-23 09:41:04.546 - RecvAgent_2 - INFO - [hook:recv] 127.0.0.1:49577 has been banned
2025-07-23 09:41:05.542 - RecvAgent_2 - INFO - [hook:recv] 127.0.0.1:49577 -> Banned? True
2025-07-23 09:41:06.541 - RecvAgent_2 - INFO - [hook:recv] 127.0.0.1:49577 -> Banned? True
2025-07-23 09:41:07.542 - RecvAgent_2 - INFO - [hook:recv] 127.0.0.1:49577 -> Banned? True
2025-07-23 09:41:08.544 - RecvAgent_2 - INFO - [hook:recv] 127.0.0.1:49577 -> Banned? True
```


```
python agents/agent_EchoAgent_2/agent.py
[DEBUG] Loaded config from: configs/client_config.json
2025-07-23 09:41:11.129 - EchoAgent_0 - INFO - Connected to server @(host=127.0.0.1, port=8888)
2025-07-23 09:41:11.548 - EchoAgent_0 - INFO - [hook:recv] 127.0.0.1:49577 passed validation
2025-07-23 09:41:11.548 - EchoAgent_0 - INFO - Buffered message from:(SocketAddress=127.0.0.1:49577).
2025-07-23 09:41:12.549 - EchoAgent_0 - INFO - [hook:recv] 127.0.0.1:49577 passed validation
2025-07-23 09:41:12.550 - EchoAgent_0 - INFO - [hook:send] sign 6fb3f
```


```
2025-07-23 09:41:12.550 - RecvAgent_2 - INFO - [hook:recv] 127.0.0.1:49577 -> Banned? True
2025-07-23 09:41:12.552 - RecvAgent_2 - INFO - [hook:recv] 127.0.0.1:49583 -> Banned? False
2025-07-23 09:41:12.556 - RecvAgent_2 - INFO - [hook:recv] 127.0.0.1:49583 valid, id=6fb3f...
2025-07-23 09:41:12.557 - RecvAgent_2 - INFO - Received message from Agent @(id=6fb3f...)
2025-07-23 09:41:12.559 - RecvAgent_2 - INFO - Agent @(id=6fb3f...) has now 1 messages stored.
2025-07-23 09:41:13.550 - RecvAgent_2 - INFO - [hook:recv] 127.0.0.1:49577 -> Banned? True
2025-07-23 09:41:13.552 - RecvAgent_2 - INFO - [hook:recv] 127.0.0.1:49583 -> Banned? False
2025-07-23 09:41:13.555 - RecvAgent_2 - INFO - [hook:recv] 127.0.0.1:49583 valid, id=6fb3f...
2025-07-23 09:41:13.555 - RecvAgent_2 - INFO - Received message from Agent @(id=6fb3f...)
2025-07-23 09:41:13.558 - RecvAgent_2 - INFO - Agent @(id=6fb3f...) has now 2 messages stored.
2025-07-23 09:41:14.553 - RecvAgent_2 - INFO - [hook:recv] 127.0.0.1:49577 -> Banned? True
2025-07-23 09:41:14.555 - RecvAgent_2 - INFO - [hook:recv] 127.0.0.1:49583 -> Banned? False
2025-07-23 09:41:14.558 - RecvAgent_2 - INFO - [hook:recv] 127.0.0.1:49583 valid, id=6fb3f...
```