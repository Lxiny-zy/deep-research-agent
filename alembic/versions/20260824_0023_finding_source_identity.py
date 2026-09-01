"""finding.source_identity：发布方独立性判定所需的身份信息

交叉印证原先按 registrable domain 判独立发布方。文献场景下这会给出**错误结论**：
一篇工作常同时存在 arXiv 预印本、期刊正式版与机构库副本，三个域一篇工作，被算成
三个独立来源，于是"已交叉印证"本身是假的。

新列存 DOI / work_id / 标题 / 作者 / 域名，由 ``EvidenceVerifier`` 在验证时刻抓取
（那是唯一同时握有 Finding 与 Source 的时刻，与 source_title / source_reference
同源的理由）。判定改为按"同一篇工作 / 同一团队"聚类。

存原始值而不是预归一化的键：归一化规则将来会改进（作者名罗马化变体、标题差异），
存原始值意味着重新判定时能受益于改进后的规则。

历史行为 NULL，此时判定退回只按 URL 域名——也就是改造前的口径，因此旧 run 的判定
可从其存下来的输入原样复现。

Revision ID: 0023
Revises: 0022
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def _has_column() -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(c["name"] == "source_identity" for c in inspector.get_columns("finding"))


def upgrade() -> None:
    if not _has_column():
        op.add_column("finding", sa.Column("source_identity", sa.JSON(), nullable=True))


def downgrade() -> None:
    if _has_column():
        op.drop_column("finding", "source_identity")
