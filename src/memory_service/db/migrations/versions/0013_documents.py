"""documents — long-form markdown content (wiki / notes / SOPs)

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-16

The fourth memory primitive after episodes, memory_blocks, and the
typed memory layer that wraps them. Holds markdown-shaped content
with a stable path inside a namespace — agent reports, imported
tickets ("save this Confluence page" / "save this Mantis bug"),
team SOPs, the user's own notes.

Storage rationale (vs filesystem) — see
docs/notes-primitive-proposal.md. Short version: DB-as-storage keeps
the chrome-extension agent on one HTTP wire, gives pg_dump a single
backup story, and keeps namespace + permission semantics consistent
with episodes + memory_blocks.

Schema highlights:

- `path` is filesystem-style or dotted ("projects/auth-refactor",
  "state.active_tasks") — caller picks. UNIQUE per (user, namespace).
- `source_url` + `imported_at` + `source` are first-class columns
  (not buried in frontmatter) so "all my Mantis imports from last
  week" is an index-backed query.
- `content_tsv` is a generated tsvector for Postgres FTS over the
  title + content; refreshed by trigger on each insert/update.
- `embedding` is added in N2 (pgvector) — column reserved but the
  HNSW index waits until embeddings are wired.
- `pinned` + `priority` are NOT here. Pinned-style "always inject"
  semantics live on memory_blocks (which stays as-is). Documents
  are search/retrieve, not always-on. A future cross-primitive
  unification can revisit.

`document_links` materializes the [[wikilink]] graph parsed from
content on each save. Backlinks are a simple reverse lookup.

`document_revisions` keeps a trail of edits for "what did this say
last week" recovery. Pruned by a future consolidate pass.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Same namespace conventions as episodes / blocks:
        # 'user:<uuid>', 'agent:<uuid>/<slug>', 'group:<slug>'.
        sa.Column("namespace", sa.String(length=255), nullable=False),

        # Stable address. Convention: filesystem-style for content
        # ('projects/auth') or dotted for state-like keys. UNIQUE
        # per (user, namespace) — same path can exist under different
        # namespaces (personal vs team SOP).
        sa.Column("path", sa.String(length=512), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),

        # Body. content_type drives editor + render behavior in
        # admin UI; defaults to markdown for the v1 use case.
        sa.Column("content", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "content_type",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'markdown'"),
        ),

        # First-class import metadata — supports the forge
        # "save this Mantis ticket / Confluence page" flow without
        # forcing a JSONB query.
        sa.Column("source", sa.String(length=64), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("imported_at", sa.TIMESTAMP(timezone=True), nullable=True),

        # Free-form structured metadata (Obsidian frontmatter, custom
        # fields). Indexed via GIN below for ad-hoc filtering.
        sa.Column(
            "frontmatter",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.String(length=64)),
            nullable=False,
            server_default=sa.text("ARRAY[]::varchar[]"),
        ),

        # Search infrastructure.
        sa.Column(
            "content_tsv",
            postgresql.TSVECTOR(),
            nullable=False,
            server_default=sa.text("''::tsvector"),
        ),
        # Reserved for N2 — pgvector. Nullable until the embedding
        # pipeline lands; the HNSW index waits with it.
        sa.Column("embedding", sa.dialects.postgresql.BYTEA(), nullable=True),

        sa.Column("word_count", sa.Integer(), nullable=False, server_default=sa.text("0")),

        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_by", sa.String(length=128), nullable=True),

        sa.UniqueConstraint(
            "user_id", "namespace", "path",
            name="uq_documents_user_namespace_path",
        ),
        sa.CheckConstraint(
            "content_type IN ('markdown','text','json','html')",
            name="ck_documents_content_type",
        ),
    )

    op.create_index(
        "ix_documents_user_namespace",
        "documents",
        ["user_id", "namespace"],
    )
    op.create_index(
        "ix_documents_tsv",
        "documents",
        ["content_tsv"],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_documents_tags",
        "documents",
        ["tags"],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_documents_frontmatter",
        "documents",
        ["frontmatter"],
        postgresql_using="gin",
    )
    # Source-filter hot path: "all my mantis imports last week".
    op.create_index(
        "ix_documents_source_time",
        "documents",
        ["user_id", "source", "imported_at"],
        postgresql_where=sa.text("source IS NOT NULL"),
    )
    # Path prefix lookups for the admin tree view.
    op.create_index(
        "ix_documents_path_pattern",
        "documents",
        ["user_id", "namespace", "path"],
    )

    # tsvector maintenance trigger. Builds from title (weight A) +
    # content (weight B) so a query that matches in the title ranks
    # ahead of one buried in the body.
    op.execute(
        """
        CREATE FUNCTION documents_tsv_refresh() RETURNS trigger AS $$
        BEGIN
          NEW.content_tsv :=
            setweight(to_tsvector('simple', coalesce(NEW.title, '')), 'A') ||
            setweight(to_tsvector('simple', coalesce(NEW.content, '')), 'B');
          NEW.word_count := array_length(
            regexp_split_to_array(coalesce(NEW.content, ''), '\\s+'),
            1
          );
          NEW.updated_at := now();
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_documents_tsv_refresh
        BEFORE INSERT OR UPDATE OF title, content ON documents
        FOR EACH ROW EXECUTE FUNCTION documents_tsv_refresh();
        """
    )

    # ─── document_links: materialized [[wikilink]] join ─────────────
    op.create_table(
        "document_links",
        sa.Column(
            "source_document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_path", sa.String(length=512), nullable=False),
        # If the source's namespace differs from "default for caller",
        # the parser records it explicitly so cross-namespace links
        # resolve unambiguously. NULL = "same namespace as the source".
        sa.Column("target_namespace", sa.String(length=255), nullable=True),
        # [[target|label]] form. NULL when the link is bare [[target]].
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint(
            "source_document_id", "target_path", "target_namespace",
            name="pk_document_links",
        ),
    )
    op.create_index(
        "ix_document_links_target",
        "document_links",
        ["target_namespace", "target_path"],
    )

    # ─── document_revisions: edit trail ─────────────────────────────
    op.create_table(
        "document_revisions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("frontmatter", postgresql.JSONB(), nullable=True),
        sa.Column(
            "revised_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("revised_by", sa.String(length=128), nullable=True),
        # Free-text reason; useful for audit. Optional.
        sa.Column("reason", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_document_revisions_doc_time",
        "document_revisions",
        ["document_id", "revised_at"],
        postgresql_ops={"revised_at": "DESC"},
    )

    # ─── episode_metadata.linked_document_paths ─────────────────────
    # Cross-primitive join: when an episode mentions a document via
    # [[wikilink]], we capture those paths so recall fan-out can
    # surface the linked documents alongside the episode hit. The
    # extension reuses TEMPER's existing episode_metadata table.
    op.add_column(
        "episode_metadata",
        sa.Column(
            "linked_document_paths",
            postgresql.ARRAY(sa.String(length=512)),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_episode_meta_linked_docs",
        "episode_metadata",
        ["linked_document_paths"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_episode_meta_linked_docs", table_name="episode_metadata")
    op.drop_column("episode_metadata", "linked_document_paths")
    op.drop_index("ix_document_revisions_doc_time", table_name="document_revisions")
    op.drop_table("document_revisions")
    op.drop_index("ix_document_links_target", table_name="document_links")
    op.drop_table("document_links")
    op.execute("DROP TRIGGER IF EXISTS trg_documents_tsv_refresh ON documents")
    op.execute("DROP FUNCTION IF EXISTS documents_tsv_refresh()")
    op.drop_index("ix_documents_path_pattern", table_name="documents")
    op.drop_index("ix_documents_source_time", table_name="documents")
    op.drop_index("ix_documents_frontmatter", table_name="documents")
    op.drop_index("ix_documents_tags", table_name="documents")
    op.drop_index("ix_documents_tsv", table_name="documents")
    op.drop_index("ix_documents_user_namespace", table_name="documents")
    op.drop_table("documents")
