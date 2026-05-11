# Jarvis

Local single-user agent cockpit inspired by `learn-claude-code/agents/s_full.py`.

## Structure

- `frontend/`: React + TypeScript cockpit UI
- `backend/`: Python runtime, API, tools, and agent orchestration
- `src-tauri/`: Tauri desktop shell
- `docs/superpowers/`: design and implementation planning artifacts

## Current State

This repository now contains the initial scaffold for:

- Tauri desktop packaging
- a cockpit-style frontend shell
- a Python backend skeleton with session, event, and agent runtime boundaries

The project still needs dependency installation before it can be run locally.

## Planned Local Commands

- Frontend: `cd frontend && npm install && npm run dev`
- Backend: `cd backend && pip install -e . && python main.py`
- Desktop shell: `cd src-tauri && cargo tauri dev`
