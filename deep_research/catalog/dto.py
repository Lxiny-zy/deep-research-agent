"""角色广场 catalog 的 Pydantic DTO：模型档案 / 角色卡片 / 搜索 key。

与 ORM 行解耦的传输对象，API 层与引擎层共用。密钥在 *View 中脱敏，
在 *Full（仅内部/引擎使用）中保留明文。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# 内置行为模板：角色卡片只能选其一（决定该角色在引擎里的执行逻辑）。
BEHAVIORS = ("plan", "research", "reflect", "synthesize", "critique")


class ModelProfileView(BaseModel):
    """对外视图：api_key 脱敏，只露是否已设置 + 尾部 hint。"""

    id: str
    name: str
    base_url: str | None = None
    model: str
    temperature: float
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
    is_default: bool = False


class AgentCardView(BaseModel):
    id: str
    name: str
    display_name: str = ""
    description: str = ""
    behavior: str
    system_prompt: str = ""
    icon: str = "🧩"
    enabled: bool = True
    model_profile_id: str | None = None
    model_profile_name: str | None = None  # 便于前端卡片直接显示绑定模型名


class SearchKeyView(BaseModel):
    """搜索 key 对外视图：api_key 脱敏。"""

    id: str
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
    icon: str = Field("🧩", max_length=16)
    enabled: bool = True
    model_profile_id: str | None = None


class AgentCardUpdate(BaseModel):
    display_name: str | None = Field(None, max_length=100)
    description: str | None = Field(None, max_length=2000)
    behavior: str | None = None
    system_prompt: str | None = Field(None, max_length=8000)
    icon: str | None = Field(None, max_length=16)
    enabled: bool | None = None
    model_profile_id: str | None = None  # 显式传 None 不区分清空——用 set_unset 语义处理
