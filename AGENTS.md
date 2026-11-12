# Mewvia AI Agents Collaboration Guide 🤖🤝🤖

This document defines the collaboration protocol between **Opencode** (Local Developer) and **Hermes** (VPS Supervisor & QA).

---

## 👥 Roles & Responsibilities

| Agent | Environment | Role | Primary Tasks |
| :--- | :--- | :--- | :--- |
| **Opencode** | Local PC (Lotus) | **Builder & Developer** | Writing features, refactoring code, crafting HTML5/CSS/JS widgets, updating logic. |
| **Hermes** | VPS Server (Production) | **Supervisor, QA & Deployer** | Pulling changes, running live Docker test suites (`pytest`, `ruff`, `mypy`), running DB migrations (`alembic`), verifying deployments, reporting back errors or confirming live status. |

---

## 🔒 Golden Rules

1. **Security & Secrets**:
   - **NEVER** commit `.env` or expose tokens (`BOT_TOKEN`, `API_ID`, `API_HASH`, database passwords).
   - `.gitignore` strictly protects `.env`, `.env.*`, and runtime cache directories.
2. **Commit Convention**:
   - Use Conventional Commits: `feat(...)`, `fix(...)`, `style(...)`, `refactor(...)`, `test(...)`.
3. **Persian (Farsi) UI**:
   - All user-facing texts, buttons, and captions must be natural, polished Persian (Farsi).
4. **Testing Gate**:
   - Every change must pass:
     - `ruff check app tests` (Linter)
     - `mypy app` (Strict type checking)
     - `pytest -p no:cacheprovider` (Unit & regression test suite)
5. **Telegram Bot Standards**:
   - Handlers must be scoped (e.g. `ChatType.PRIVATE` where applicable to avoid group chat leaks).
   - Videos must be encoded with H.264/AAC and faststart for iOS Photos/Gallery streamability.

---

## 🔄 Workflow Cycle

```
[User Request] 
      │
      ▼
[Opencode on Local PC] ──(Develops & Commits)──► [Push to GitHub]
                                                        │
                                                        ▼
[Hermes on VPS] ◄────(git pull & tests)─────────────────┘
      │
      ├───► Tests Failed ───► Hermes gives exact error output to User -> passed to Opencode
      │
      └───► Tests Passed ───► Hermes restarts bot, verifies container health, confirms live!
```

---

## 📋 Collaboration Task Board

### Active State
- **Branch**: `master`
- **Supervisor**: Hermes (VPS)
- **Status**: Ready for collaborative work

### Communication Log
- **Hermes**: Repository initialized and synchronized with GitHub. All 416 existing tests passing.
