export type SessionSummary = {
  session_id: string;
  title: string;
  workspace_mode: "bound" | "default";
  canonical_workspace_path: string;
  workspace_label: string;
  workspace_fingerprint: string;
  status: string;
  created_at: string;
  updated_at: string;
};

export type TimelineEvent = {
  session_id: string;
  type: string;
  content: string;
  created_at: string;
};

export type TaskSummary = {
  id: number;
  subject: string;
  description: string;
  status: string;
  owner: string | null;
  created_at: string;
};

export type ApprovalSummary = {
  id: number;
  session_id: string | null;
  approval_type: string;
  status: string;
  prompt: string;
  feedback: string | null;
  created_at: string;
};

export type TeammateSummary = {
  id: number;
  session_id: string | null;
  name: string;
  role: string;
  kind: string;
  status: string;
  created_at: string;
};

export type TeammateMessageSummary = {
  id: number;
  agent_id: number;
  direction: string;
  content: string;
  created_at: string;
};

export type SubagentSummary = {
  id: number;
  session_id: string | null;
  name: string;
  role: string;
  kind: string;
  status: string;
  created_at: string;
};

export type ToolExecutionSummary = {
  id: number;
  session_id: string;
  tool_name: string;
  status: string;
  input_json: string | null;
  output_text: string | null;
  created_at: string;
};

export type SkillSummary = {
  name: string;
  path: string;
};

export type SessionMemorySummary = {
  id: number;
  session_id: string;
  kind: string;
  content: string;
  source_turn_id: number | null;
  path_ref: string | null;
  salience: number;
  status: string;
  created_at: string;
  updated_at: string;
};

export type SessionAssetSummary = {
  id: string;
  session_id: string;
  kind: string;
  mime_type: string;
  filename: string;
  size_bytes: number;
  sha256: string;
  storage_path: string;
  preview_path: string | null;
  status: string;
  error_message: string | null;
  created_at: string;
  updated_at: string;
};

export type TurnSummary = {
  id: number;
  session_id: string;
  user_message_id: number | null;
  workspace_path: string | null;
  workspace_fingerprint: string | null;
  status: string;
  started_at: string;
  updated_at: string;
  completed_at: string | null;
  last_checkpoint_seq: number;
  resume_hint: string | null;
  error_summary: string | null;
  resumable: boolean;
};

export type SessionStateSummary = {
  session: SessionSummary;
  active_turn: TurnSummary | null;
  latest_interrupted_turn: TurnSummary | null;
  latest_waiting_approval_turn: TurnSummary | null;
  rolling_summary: string | null;
};

export type WorkspaceResolveSummary = {
  workspace_path: string;
  workspace_label: string;
  workspace_fingerprint: string;
};

const API_BASE = "http://127.0.0.1:8731/api";

export async function fetchBootstrap(): Promise<{
  app: string;
  sessions: SessionSummary[];
  panels: string[];
  tasks: TaskSummary[];
  teammates: TeammateSummary[];
  subagents: SubagentSummary[];
  approvals: ApprovalSummary[];
  tool_executions: ToolExecutionSummary[];
}> {
  const response = await fetch(`${API_BASE}/bootstrap`);
  if (!response.ok) {
    throw new Error("Failed to load bootstrap state");
  }
  return response.json();
}

export async function fetchSkills(): Promise<SkillSummary[]> {
  const response = await fetch(`${API_BASE}/skills`);
  if (!response.ok) {
    throw new Error("Failed to load skills");
  }
  return response.json();
}

export async function resolveWorkspace(content: string): Promise<WorkspaceResolveSummary | null> {
  const response = await fetch(`${API_BASE}/workspaces/resolve`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ content }),
  });
  if (!response.ok) {
    throw new Error("Failed to resolve workspace");
  }
  return response.json();
}

export async function createSession(title: string, workspacePath?: string): Promise<SessionSummary> {
  const workspaceMode = workspacePath ? "bound" : "default";
  const response = await fetch(`${API_BASE}/sessions`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ title, workspace_mode: workspaceMode, workspace_path: workspacePath ?? null }),
  });
  if (!response.ok) {
    throw new Error("Failed to create session");
  }
  return response.json();
}

export async function fetchSessions(): Promise<SessionSummary[]> {
  const response = await fetch(`${API_BASE}/sessions`);
  if (!response.ok) {
    throw new Error("Failed to load sessions");
  }
  return response.json();
}

export async function renameSession(sessionId: string, title: string): Promise<SessionSummary> {
  const response = await fetch(`${API_BASE}/sessions/${sessionId}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ title }),
  });
  if (!response.ok) {
    throw new Error("Failed to rename session");
  }
  return response.json();
}

export async function deleteSession(sessionId: string): Promise<void> {
  const response = await fetch(`${API_BASE}/sessions/${sessionId}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    throw new Error("Failed to delete session");
  }
}

export async function sendMessage(
  sessionId: string,
  content: string,
  assetIds: string[] = [],
): Promise<void> {
  const response = await fetch(`${API_BASE}/sessions/${sessionId}/messages`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      role: "user",
      content,
      asset_ids: assetIds,
    }),
  });
  if (!response.ok) {
    throw new Error("Failed to send message");
  }
}

export async function stopSessionTurn(sessionId: string): Promise<boolean> {
  const response = await fetch(`${API_BASE}/sessions/${sessionId}/stop`, {
    method: "POST",
  });
  if (!response.ok) {
    throw new Error("Failed to stop session turn");
  }
  const payload = await response.json();
  return Boolean(payload.stopped);
}

export async function fetchTimeline(sessionId: string): Promise<TimelineEvent[]> {
  const response = await fetch(`${API_BASE}/sessions/${sessionId}/timeline`);
  if (!response.ok) {
    throw new Error("Failed to load timeline");
  }
  return response.json();
}

export async function fetchSessionState(sessionId: string): Promise<SessionStateSummary> {
  const response = await fetch(`${API_BASE}/sessions/${sessionId}/state`);
  if (!response.ok) {
    throw new Error("Failed to load session state");
  }
  return response.json();
}

export async function fetchSessionAssets(sessionId: string): Promise<SessionAssetSummary[]> {
  const response = await fetch(`${API_BASE}/sessions/${sessionId}/assets`);
  if (!response.ok) {
    throw new Error("Failed to load session assets");
  }
  return response.json();
}

export async function uploadSessionAssets(sessionId: string, files: File[]): Promise<SessionAssetSummary[]> {
  const form = new FormData();
  for (const file of files) {
    form.append("files", file);
  }
  const response = await fetch(`${API_BASE}/sessions/${sessionId}/assets`, {
    method: "POST",
    body: form,
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || "Failed to upload session assets");
  }
  return response.json();
}

export async function deleteSessionAsset(sessionId: string, assetId: string): Promise<void> {
  const response = await fetch(`${API_BASE}/sessions/${sessionId}/assets/${assetId}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    throw new Error("Failed to delete session asset");
  }
}

export async function fetchTurns(sessionId?: string): Promise<TurnSummary[]> {
  const url = sessionId
    ? `${API_BASE}/turns?session_id=${encodeURIComponent(sessionId)}`
    : `${API_BASE}/turns`;
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error("Failed to load turns");
  }
  return response.json();
}

export async function resumeTurn(turnId: number): Promise<boolean> {
  const response = await fetch(`${API_BASE}/turns/${turnId}/resume`, {
    method: "POST",
  });
  if (!response.ok) {
    throw new Error("Failed to resume turn");
  }
  const payload = await response.json();
  return Boolean(payload.accepted);
}

export async function createTask(subject: string, sessionId: string): Promise<TaskSummary> {
  const response = await fetch(`${API_BASE}/tasks`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      subject,
      session_id: sessionId,
      description: "",
    }),
  });
  if (!response.ok) {
    throw new Error("Failed to create task");
  }
  return response.json();
}

export async function fetchToolExecutions(
  sessionId?: string,
): Promise<ToolExecutionSummary[]> {
  const url = sessionId
    ? `${API_BASE}/tool-executions?session_id=${encodeURIComponent(sessionId)}`
    : `${API_BASE}/tool-executions`;
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error("Failed to load tool executions");
  }
  return response.json();
}

export async function fetchSessionMemory(sessionId: string): Promise<SessionMemorySummary[]> {
  const response = await fetch(`${API_BASE}/session-memory?session_id=${encodeURIComponent(sessionId)}`);
  if (!response.ok) {
    throw new Error("Failed to load session memory");
  }
  return response.json();
}

export async function fetchApprovals(sessionId?: string): Promise<ApprovalSummary[]> {
  const url = sessionId
    ? `${API_BASE}/approvals?session_id=${encodeURIComponent(sessionId)}`
    : `${API_BASE}/approvals`;
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error("Failed to load approvals");
  }
  return response.json();
}

export async function decideApproval(
  approvalId: number,
  approve: boolean,
): Promise<ApprovalSummary> {
  const response = await fetch(`${API_BASE}/approvals/${approvalId}/decision`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      approve,
      feedback: "",
    }),
  });
  if (!response.ok) {
    throw new Error("Failed to decide approval");
  }
  return response.json();
}

export async function fetchTeammates(sessionId?: string): Promise<TeammateSummary[]> {
  const url = sessionId
    ? `${API_BASE}/teammates?session_id=${encodeURIComponent(sessionId)}`
    : `${API_BASE}/teammates`;
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error("Failed to load teammates");
  }
  return response.json();
}

export async function createTeammate(
  sessionId: string,
  name: string,
  role: string,
): Promise<TeammateSummary> {
  const response = await fetch(`${API_BASE}/teammates`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      session_id: sessionId,
      name,
      role,
    }),
  });
  if (!response.ok) {
    throw new Error("Failed to create teammate");
  }
  return response.json();
}

export async function fetchTeammateMessages(agentId: number): Promise<TeammateMessageSummary[]> {
  const response = await fetch(`${API_BASE}/teammates/${agentId}/messages`);
  if (!response.ok) {
    throw new Error("Failed to load teammate messages");
  }
  return response.json();
}

export async function sendTeammateMessage(
  agentId: number,
  content: string,
): Promise<{ sent: TeammateMessageSummary; reply: TeammateMessageSummary }> {
  const response = await fetch(`${API_BASE}/teammates/${agentId}/messages`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ content }),
  });
  if (!response.ok) {
    throw new Error("Failed to send teammate message");
  }
  return response.json();
}

export async function fetchSubagents(sessionId?: string): Promise<SubagentSummary[]> {
  const url = sessionId
    ? `${API_BASE}/subagents?session_id=${encodeURIComponent(sessionId)}`
    : `${API_BASE}/subagents`;
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error("Failed to load subagents");
  }
  return response.json();
}

export async function runSubagent(
  sessionId: string,
  name: string,
  prompt: string,
): Promise<{ subagent: SubagentSummary; summary: string }> {
  const response = await fetch(`${API_BASE}/subagents`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      session_id: sessionId,
      name,
      prompt,
    }),
  });
  if (!response.ok) {
    throw new Error("Failed to run subagent");
  }
  return response.json();
}

export function openSessionEvents(
  sessionId: string,
  onEvent: (event: TimelineEvent) => void,
  handlers?: {
    onOpen?: () => void;
    onClose?: () => void;
    onError?: () => void;
  },
): () => void {
  const socket = new WebSocket(`ws://127.0.0.1:8731/api/sessions/${sessionId}/events`);
  let manuallyClosed = false;
  socket.onopen = () => handlers?.onOpen?.();
  socket.onmessage = (message) => {
    onEvent(JSON.parse(message.data) as TimelineEvent);
  };
  socket.onerror = () => handlers?.onError?.();
  socket.onclose = () => {
    if (!manuallyClosed) {
      handlers?.onClose?.();
    }
  };
  return () => {
    manuallyClosed = true;
    socket.close();
  };
}
