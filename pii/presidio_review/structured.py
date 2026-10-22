"""
Column-level PII detection and anonymization with Presidio Structured.

For each table in a CSV/XLSX file, presidio-structured's PandasAnalysisBuilder
decides which entity type (if any) a column holds. Each such column becomes one
"column" row in the review CSV. If a reviewer marks it REDACT, StructuredEngine
anonymizes every value in that column.

Free-text columns (comments, notes) are deliberately left out of column-level
mapping: replacing a whole sentence because it contains a name would destroy the
data. Their cells are still analyzed one by one at the span level.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median

import pandas as pd
from presidio_anonymizer.entities import OperatorConfig
from presidio_structured import PandasAnalysisBuilder, StructuredAnalysis, StructuredEngine
from presidio_structured.analysis_builder import NON_PII_ENTITY_TYPE
from presidio_structured.data.data_processors import PandasDataProcessor

from pii_utils import clean


@dataclass
class StructuredSettings:
    enabled: bool = True
    strategy: str = "most_common"   # presidio-structured: most_common | highest_confidence | mixed
    sample_rows: int = 500          # rows sampled per table for detection
    min_match_ratio: float = 0.3    # share of non-empty cells that must contain the entity
    free_text_words: int = 5        # columns whose median value has more words are free text

    def to_dict(self):
        return self.__dict__.copy()


@dataclass
class ColumnFinding:
    table: int
    col: int
    header: str
    entity_type: str
    matched: int
    non_empty: int
    samples: list

    @property
    def ratio(self):
        return self.matched / self.non_empty if self.non_empty else 0.0


def _frame(doc, t, cols):
    """DataFrame of the given columns with safe column names (presidio-structured
    reads columns via getattr, which breaks on names like 'Email Address')."""
    rows = None
    data = {}
    for c in cols:
        values = doc.column_values(t, c)
        rows = [r for r, _ in values]
        data[f"c{c}"] = [v for _, v in values]
    return pd.DataFrame(data, dtype=object), rows or []


def analyze_columns(doc, analyzer, settings: StructuredSettings, language="en",
                    score_threshold=0.35) -> list[ColumnFinding]:
    findings = []
    builder = PandasAnalysisBuilder(analyzer=analyzer, analyzer_score_threshold=score_threshold)
    for t, table in enumerate(doc.tables):
        candidates = []
        for c in table.headers:
            values = [v for _, v in doc.column_values(t, c) if v.strip()]
            if values and median(len(v.split()) for v in values) <= settings.free_text_words:
                candidates.append(c)
        if not candidates:
            continue
        df, _ = _frame(doc, t, candidates)
        sample = df.sample(min(settings.sample_rows, len(df)), random_state=123)

        # Same steps PandasAnalysisBuilder.generate_analysis() runs, kept separate so
        # we can also report how many cells matched (reviewers need that to judge it).
        results = builder._batch_analyze_df(sample, language)
        for key, per_cell in results.items():
            # the builder only applies its threshold when it creates its own analyzer;
            # also drop fragments (URL inside an email) so they can't win the vote
            per_cell = [clean([x for x in r if x.score >= score_threshold]) for r in per_cell]
            chosen = builder._find_entity_based_on_strategy(
                per_cell, settings.strategy, 0.5)
            if chosen.entity_type == NON_PII_ENTITY_TYPE:
                continue
            c = int(key[1:])
            cells = list(sample[key])
            non_empty = sum(1 for v in cells if str(v).strip())
            matched = sum(1 for r in per_cell if any(x.entity_type == chosen.entity_type for x in r))
            if not non_empty or matched / non_empty < settings.min_match_ratio:
                continue
            samples = [v for v in cells if str(v).strip()][:3]
            findings.append(ColumnFinding(t, c, table.headers[c], chosen.entity_type,
                                          matched, non_empty, samples))
    return findings


def operators_for(entity_types, operator: str):
    """Operator config per entity type. Through StructuredEngine, Presidio's 'replace'
    doesn't know the entity type, so the <TYPE> label is passed explicitly."""
    ops = {}
    for et in set(entity_types):
        if operator == "replace":
            ops[et] = OperatorConfig("replace", {"new_value": f"<{et}>"})
        elif operator == "redact":
            ops[et] = OperatorConfig("redact", {})
        elif operator == "mask":
            ops[et] = OperatorConfig("mask", {"masking_char": "*", "chars_to_mask": 10_000,
                                              "from_end": False})
        elif operator == "hash":
            ops[et] = OperatorConfig("hash", {"hash_type": "sha256"})
        else:
            raise ValueError(operator)
    return ops


def anonymize_columns(doc, approved, operator: str) -> dict:
    """approved: list of (table, col, entity_type). Returns {cell_key: new_value}."""
    overrides = {}
    engine = StructuredEngine(PandasDataProcessor())
    by_table: dict = {}
    for t, c, et in approved:
        by_table.setdefault(t, {})[c] = et
    for t, cols in by_table.items():
        df, rows = _frame(doc, t, list(cols))
        original = df.copy()
        analysis = StructuredAnalysis(entity_mapping={f"c{c}": et for c, et in cols.items()})
        out = engine.anonymize(df, analysis, operators=operators_for(cols.values(), operator))
        sheet = doc.tables[t].name
        for c in cols:
            for i, r in enumerate(rows):
                before, after = original.at[i, f"c{c}"], out.at[i, f"c{c}"]
                if str(before).strip() and after != before:  # leave empty cells empty
                    overrides[(sheet, r, c)] = after
    return overrides
