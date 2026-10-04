"""Read-only guidance for cancellation settlement; never changes task state."""

from datetime import UTC, datetime

from .persistence.repository import EXECUTION_LEASE_SECONDS, RunDetail


def cancellation_notice(detail: RunDetail) -> str | None:
    requested = detail.cancel_requested_at
    if detail.status != "cancelling" or requested is None:
        return None
    if requested.tzinfo is None:
        requested = requested.replace(tzinfo=UTC)
    if (datetime.now(UTC) - requested).total_seconds() <= EXECUTION_LEASE_SECONDS:
        return None
    return (
        "取消请求已保存，仍在等待执行服务完成取消。等待时间已超过两分钟；"
        "请联系管理员检查执行服务是否正常，无需重复提交取消请求。"
    )
