# Summoner Agent Library

## Agent formatting and desktop app compatibility

## Agent collection


## 🧠 Agent Library Overview

### Legend
- **DB**: Uses a database (e.g. `asqlite`)  
- **Queue**: Uses an async queue  
- **Flows**: Uses modular flow architecture  
- **Triggers**: Uses triggers to orchestrate behavior  
- **Hooks**: Includes hooks to preprocess/postprocess messages  
- **Template**: Usable as a base for other agents  
- **Composable**: Designed to work as part of a multi-agent system

### Table

The following table categorizes available agents by skill level, use case, and technical features.

| Agent Name | Level    | Use Case     | Features               | DB | Queue | Flows | Triggers | Hooks | Template | Composable |
|------------|----------|--------------|------------------------|----|--------|--------|----------|--------|----------|-------------|
| `echo_bot` | Beginner | MCP          | core                   | ❌  | ❌     | ❌     | ❌       | ✅     | ✅       | ❌          |
| `finance_analyst` | Medium   | Finance      | smart_tools       | ✅  | ✅     | ✅     | ✅       | ✅     | ❌       | ✅          |
| `kobold_assistant` | Advanced | Research     | kobold            | ✅  | ✅     | ✅     | ✅       | ✅     | ✅       | ✅          |
| `industry_scheduler` | Medium | Industry     | core            | ❌  | ✅     | ✅     | ✅       | ❌     | ✅       | ✅          |
| `template_agent` | Beginner | General      | core                   | ❌  | ❌     | ❌     | ❌       | ❌     | ✅       | ✅          |




send
receive
echo

chat: user control

relay: delay, report and sequence

glue: connector

api: reddit, gpt,