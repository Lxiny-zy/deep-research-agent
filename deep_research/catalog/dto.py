"""角色广场 catalog 的 Pydantic DTO：模型档案 / 角色卡片 / 搜索 key。

与 ORM 行解耦的传输对象，API 层与引擎层共用。密钥在 *View 中脱敏，
在 *Full（仅内部/引擎使用）中保留明文。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# 内置行为模板：角色卡片只能选其一（决定该角色在引擎里的执行逻辑）。
BEHAVIORS = ("plan", "research", "reflect", "synthesize", "critique")

SearchProvider = Literal[
    "tavily", "brave", "serper", "grok", "openalex", "arxiv", "responses", "chat_search"
]
PromptMode = Literal["append", "replace"]


class SearchProfileInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    provider: SearchProvider
    endpoint: str = Field("", max_length=500)
    model: str = Field("", max_length=100)
    key_ids: list[str] = Field(default_factory=list, max_length=100)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_service(self) -> SearchProfileInput:
        self.name = self.name.strip()
        self.endpoint = self.endpoint.strip()
        self.model = self.model.strip()
        self.key_ids = list(dict.fromkeys(self.key_ids))
        if not self.name:
            raise ValueError("检索档案名称不能为空")
        if self.provider in {"responses", "chat_search"} and not (self.endpoint and self.model):
            raise ValueError("外接搜索模型必须填写完整请求端点和模型名称")
        if self.provider not in {"responses", "chat_search", "grok"} and (
            self.endpoint or self.model
        ):
            raise ValueError("此渠道使用内置接口，无需设置模型或端点")
        if self.provider in {"openalex", "arxiv"} and self.key_ids:
            raise ValueError("此渠道不使用 API Key")
        return self


class SearchProfileView(BaseModel):
    id: str
    name: str
    provider: SearchProvider
    endpoint: str = ""
    model: str = ""
    # None is reserved for built-in profiles: resolve the provider's legacy pool.
    key_ids: list[str] | None = None
    enabled: bool = True
    builtin: bool = False
    key_order_frozen: bool = False


class ModelProfileView(BaseModel):
    """对外视图：api_key 脱敏，只露是否已设置 + 尾部 hint。"""

    id: str
    name: str
    base_url: str | None = None
    model: str
    temperature: float
    parameter_mode: str = "temperature"
    reasoning_effort: str = "medium"
    is_default: bool
    api_key_set: bool
    api_key_hint: str


class ModelProfileFull(BaseModel):
    """内部视图：含明文 api_key，供引擎构造 LLM。不下发前端。"""

    id: str
    name: str
    base_url: str | None = None
    api_key: str = ""
    model: str
    temperature: float = 0.3
    parameter_mode: str = "temperature"
    reasoning_effort: str = "medium"
    is_default: bool = False


class AgentCardView(BaseModel):
    id: str
    name: str
    display_name: str = ""
    description: str = ""
    behavior: str
    system_prompt: str = ""
    prompt_mode: PromptMode = "replace"
    search_profile_ids: list[str] | None = None
    icon: str = "🧩"
    enabled: bool = True
    model_profile_id: str | None = None
    model_profile_name: str | None = None  # 便于前端卡片直接显示绑定模型名


class AgentCardSnapshot(BaseModel):
    """Non-secret role semantics persisted with a recoverable run."""

    name: str
    behavior: str
    system_prompt: str = ""
    prompt_mode: PromptMode = "replace"
    search_profile_ids: list[str] | None = None
    model_profile_id: str | None = None


class ModelProfileSnapshot(BaseModel):
    """Non-secret model execution parameters frozen for recovery and audit."""

    id: str
    name: str
    base_url: str | None = None
    model: str
    temperature: float = 0.3
    parameter_mode: str = "temperature"
    reasoning_effort: str = "medium"
    is_default: bool = False


class CatalogRuntimeSnapshot(BaseModel):
    """Catalog inputs needed to replay a run without live role-card state.

    Profile IDs are references only.  Secrets remain in the catalog repository
    and are deliberately not serialized into the checkpoint.
    """

    version: Literal[1] = 1
    cards: list[AgentCardSnapshot] = Field(default_factory=list)
    profiles: list[ModelProfileSnapshot] = Field(default_factory=list)
    default_profile_id: str | None = None
    terminal_roles: list[str] = Field(default_factory=list)
    search_profiles: list[SearchProfileView] = Field(default_factory=list)
    default_search_profile_ids: list[str] | None = None


class SearchKeyView(BaseModel):
    """搜索 key 对外视图：api_key 脱敏。"""

    id: str
    provider: str = "tavily"
    label: str = ""
    priority: int = 0
    enabled: bool = True
    api_key_hint: str


class AgentCardCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    display_name: str = Field("", max_length=100)
    description: str = Field("", max_length=2000)
    behavior: str
    system_prompt: str = Field("", max_length=8000)
    prompt_mode: PromptMode = "append"
    search_profile_ids: list[str] | None = Field(None, min_length=1, max_length=12)
    icon: str = Field("🧩", max_length=16)
    enabled: bool = True
    model_profile_id: str | None = None


class AgentCardUpdate(BaseModel):
    display_name: str | None = Field(None, max_length=100)
    description: str | None = Field(None, max_length=2000)
    behavior: str | None = None
    system_prompt: str | None = Field(None, max_length=8000)
    prompt_mode: PromptMode | None = None
    search_profile_ids: list[str] | None = Field(None, min_length=1, max_length=12)
    icon: str | None = Field(None, max_length=16)
    enabled: bool | None = None
    model_profile_id: str | None = None  # 显式传 None 不区分清空——用 set_unset 语义处理

    @field_validator(
        "display_name",
        "description",
        "behavior",
        "system_prompt",
        "prompt_mode",
        "icon",
        "enabled",
        mode="before",
    )
    @classmethod
    def reject_null_non_nullable_fields(cls, value: object) -> object:
        if value is None:
            raise ValueError("field cannot be null; omit it to keep the current value")
        return value


class WorkflowDefView(BaseModel):
    """自定义工作流对外视图（无密钥，steps 原样透出供前端编辑/后端运行）。"""

    id: str
    name: str
    display_name: str = ""
    description: str = ""
    steps: list[dict] = Field(default_factory=list)
    nodes: list[dict] = Field(default_factory=list)
    edges: list[dict] = Field(default_factory=list)
    viewport: dict = Field(default_factory=dict)
    version: int = 1
    enabled: bool = True


class WorkflowDefCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    display_name: str = Field("", max_length=100)
    description: str = Field("", max_length=2000)
    steps: list[dict] = Field(default_factory=list)
    nodes: list[dict] = Field(default_factory=list)
    edges: list[dict] = Field(default_factory=list)
    viewport: dict = Field(default_factory=dict)
    version: int = Field(1, ge=1)
    enabled: bool = True


class WorkflowDefUpdate(BaseModel):
    display_name: str | None = Field(None, max_length=100)
    description: str | None = Field(None, max_length=2000)
    steps: list[dict] | None = None
    nodes: list[dict] | None = None
    edges: list[dict] | None = None
    viewport: dict | None = None
    version: int | None = Field(None, ge=1)
    enabled: bool | None = None

    @field_validator(
        "display_name",
        "description",
        "steps",
        "nodes",
        "edges",
        "viewport",
        "version",
        "enabled",
        mode="before",
    )
    @classmethod
    def reject_null_fields(cls, value: object) -> object:
        if value is None:
            raise ValueError("field cannot be null; omit it to keep the current value")
        return value
