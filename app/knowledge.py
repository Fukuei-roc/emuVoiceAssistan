from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.models import KnowledgeSearchResult, KnowledgeSection, KnowledgeStatus

logger = logging.getLogger(__name__)


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def normalize_query(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def load_markdown(path: Path, vehicle: str) -> list[KnowledgeSection]:
    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines()
    sections: list[KnowledgeSection] = []
    current_heading: str | None = None
    current_lines: list[str] = []

    for line in lines:
        match = HEADING_RE.match(line)
        if match and len(match.group(1)) == 2:
            if current_heading:
                sections.append(
                    KnowledgeSection(
                        vehicle=vehicle,
                        heading=current_heading,
                        content="\n".join(current_lines).strip(),
                        source=path.name,
                    )
                )
            current_heading = match.group(2).strip()
            current_lines = [line]
        elif current_heading:
            current_lines.append(line)

    if current_heading:
        sections.append(
            KnowledgeSection(
                vehicle=vehicle,
                heading=current_heading,
                content="\n".join(current_lines).strip(),
                source=path.name,
            )
        )

    return sections


class MarkdownKnowledgeBase:
    def __init__(self, db_path: Path, markdown_path: Path | list[Path], vehicle: str) -> None:
        self.db_path = db_path
        self.markdown_paths = [markdown_path] if isinstance(markdown_path, Path) else markdown_path
        self.markdown_path = self.markdown_paths[0] if self.markdown_paths else Path()
        self.vehicle = vehicle
        self.loaded_at: datetime | None = None
        self.section_count = 0

    def reload(self) -> None:
        missing_paths = [path for path in self.markdown_paths if not path.exists()]
        if missing_paths:
            raise FileNotFoundError(f"Markdown knowledge file not found: {missing_paths[0]}")

        sections: list[KnowledgeSection] = []
        for path in self.markdown_paths:
            sections.extend(load_markdown(path, self.vehicle))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DROP TABLE IF EXISTS knowledge_sections")
            conn.execute("DROP TABLE IF EXISTS knowledge_sections_fts")
            conn.execute(
                """
                CREATE TABLE knowledge_sections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    vehicle TEXT NOT NULL,
                    heading TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE VIRTUAL TABLE knowledge_sections_fts USING fts5(
                    heading,
                    content,
                    content='knowledge_sections',
                    content_rowid='id',
                    tokenize='unicode61'
                )
                """
            )
            for section in sections:
                cursor = conn.execute(
                    """
                    INSERT INTO knowledge_sections (vehicle, heading, content, source)
                    VALUES (?, ?, ?, ?)
                    """,
                    (section.vehicle, section.heading, section.content, section.source),
                )
                section_id = cursor.lastrowid
                conn.execute(
                    "INSERT INTO knowledge_sections_fts(rowid, heading, content) VALUES (?, ?, ?)",
                    (section_id, section.heading, section.content),
                )
            conn.commit()

        self.section_count = len(sections)
        self.loaded_at = datetime.now(timezone.utc)
        logger.info(
            "Knowledge loaded: vehicle=%s sections=%s files=%s",
            self.vehicle,
            self.section_count,
            self.markdown_paths,
        )

    def status(self) -> KnowledgeStatus:
        modified_times = [
            path.stat().st_mtime
            for path in self.markdown_paths
            if path.exists()
        ]
        modified_at = None
        if modified_times:
            modified_at = datetime.fromtimestamp(max(modified_times), timezone.utc).isoformat()
        return KnowledgeStatus(
            vehicle=self.vehicle,
            file=str(self.markdown_path),
            files=[str(path) for path in self.markdown_paths],
            sections=self.section_count,
            loaded_at=self.loaded_at.isoformat() if self.loaded_at else None,
            file_modified_at=modified_at,
        )

    def search(self, query: str, limit: int = 5) -> list[KnowledgeSearchResult]:
        query = query.strip()
        if not query:
            return []

        results: list[KnowledgeSearchResult] = []
        seen: set[str] = set()

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            self._append_like_results(conn, query, limit, results, seen)
            if len(results) < limit:
                self._append_fts_results(conn, query, limit, results, seen)
            if len(results) < limit:
                self._append_substring_results(conn, query, limit, results, seen)

        logger.info("Search query=%r headings=%s", query, [item.heading for item in results])
        return results[:limit]

    def _append_like_results(
        self,
        conn: sqlite3.Connection,
        query: str,
        limit: int,
        results: list[KnowledgeSearchResult],
        seen: set[str],
    ) -> None:
        compact_query = normalize_query(query)
        rows = conn.execute(
            "SELECT id, heading, content, source FROM knowledge_sections WHERE vehicle = ?",
            (self.vehicle,),
        ).fetchall()
        scored = []
        for row in rows:
            compact_heading = normalize_query(row["heading"])
            compact_content = normalize_query(row["content"])
            if compact_query in compact_heading:
                scored.append((0, row))
            elif all(token and token in compact_heading for token in self._query_tokens(query)):
                scored.append((1, row))
            elif compact_query in compact_content:
                scored.append((2, row))
        for _, row in sorted(scored, key=lambda item: item[0]):
            self._add_row(row, results, seen)
            if len(results) >= limit:
                return

    def _append_fts_results(
        self,
        conn: sqlite3.Connection,
        query: str,
        limit: int,
        results: list[KnowledgeSearchResult],
        seen: set[str],
    ) -> None:
        fts_query = self._fts_query(query)
        if not fts_query:
            return
        try:
            rows = conn.execute(
                """
                SELECT s.id, s.heading, s.content, s.source
                FROM knowledge_sections_fts f
                JOIN knowledge_sections s ON s.id = f.rowid
                WHERE knowledge_sections_fts MATCH ? AND s.vehicle = ?
                ORDER BY bm25(knowledge_sections_fts)
                LIMIT ?
                """,
                (fts_query, self.vehicle, limit),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            logger.warning("FTS query failed: query=%r error=%s", query, exc)
            return
        for row in rows:
            self._add_row(row, results, seen)
            if len(results) >= limit:
                return

    def _append_substring_results(
        self,
        conn: sqlite3.Connection,
        query: str,
        limit: int,
        results: list[KnowledgeSearchResult],
        seen: set[str],
    ) -> None:
        compact_query = normalize_query(query)
        tokens = self._query_tokens(query)
        rows = conn.execute(
            "SELECT id, heading, content, source FROM knowledge_sections WHERE vehicle = ?",
            (self.vehicle,),
        ).fetchall()
        scored = []
        for row in rows:
            haystack = normalize_query(row["heading"] + "\n" + row["content"])
            score = 0
            if compact_query in haystack:
                score += 5
            score += sum(1 for token in tokens if token in haystack)
            if score:
                scored.append((-score, row))
        for _, row in sorted(scored, key=lambda item: item[0]):
            self._add_row(row, results, seen)
            if len(results) >= limit:
                return

    def _add_row(self, row: sqlite3.Row, results: list[KnowledgeSearchResult], seen: set[str]) -> None:
        key = str(row["id"])
        if key in seen:
            return
        seen.add(key)
        results.append(KnowledgeSearchResult(heading=row["heading"], content=row["content"], source=row["source"]))

    def _query_tokens(self, query: str) -> list[str]:
        raw_tokens = re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+", query)
        tokens = [token.lower() for token in raw_tokens if token.strip()]
        compact = normalize_query(query)
        if compact and compact not in tokens:
            tokens.append(compact)
        if "vcb" in compact and "不閉合" in compact:
            tokens.extend(["vcb", "不閉合"])
        return list(dict.fromkeys(tokens))

    def _fts_query(self, query: str) -> str:
        tokens = [token for token in self._query_tokens(query) if len(token) > 1]
        return " OR ".join(f'"{token}"' for token in tokens[:8])
