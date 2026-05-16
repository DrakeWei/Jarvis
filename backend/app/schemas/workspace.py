from pydantic import BaseModel, Field


class WorkspaceResolveRequest(BaseModel):
    content: str = Field(min_length=1)


class WorkspaceResolveSummary(BaseModel):
    workspace_path: str
    workspace_label: str
    workspace_fingerprint: str
