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

## Local Run

1. Install backend dependencies:
   `./.venv/bin/python -m pip install -e backend`
2. Install frontend and Tauri CLI dependencies:
   `cd frontend && npm install`
3. Start the desktop app:
   `cd frontend && npm run tauri:dev`

The Tauri shell now auto-starts the Python backend if `127.0.0.1:8731` is not already in use.
