#!/usr/bin/env python3
"""
Knowledge Base File Loader
Reads Excel files from the Knowledgebase/ folder, caches them as reference
profiles, and provides helper functions to inject reference-only guidance into
AI prompts.

- On startup the module scans the Knowledgebase/ directory for .xlsx/.xls/.csv
    files, reads each one with pandas, and stores a compact schema/concept
    summary.
- Knowledgebase content is reference-only: it should help the model recognize
    structures and equivalent meanings in live user data, but must not be quoted
    back as answer evidence.
- `get_kb_context(user_query)` returns a ready-to-inject reference profile for
    the AI prompt, filtered toward the concepts relevant to the user query.
- `reload_kb_files()` can be called at runtime to pick up new/changed files.
"""

import os
import glob
import hashlib
import re
from datetime import datetime

_APP_ROOT = os.path.dirname(os.path.abspath(__file__))
KB_FOLDER = os.path.join(_APP_ROOT, 'Knowledgebase')

# Files with more rows than this get summarised instead of fully inlined
MAX_INLINE_ROWS = 300

# In-memory cache: { filename: { 'text': ..., 'rows': ..., 'hash': ...,
#                                  'df': DataFrame (for large files),
#                                  'summary': str, 'is_large': bool } }
_kb_cache = {}
_last_loaded = None


def _file_hash(filepath):
    """Quick MD5 of a file to detect changes."""
    h = hashlib.md5()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()


def _clean_dataframe(df):
    """Drop empty rows/cols and fix Unnamed headers."""
    import pandas as pd
    df = df.dropna(how='all').dropna(axis=1, how='all')
    if df.empty:
        return df

    # Clean up column names that are 'Unnamed: X'
    clean_cols = []
    for i, col in enumerate(df.columns):
        if str(col).startswith('Unnamed'):
            first_val = df[col].dropna().iloc[0] if not df[col].dropna().empty else f'Column_{i}'
            clean_cols.append(str(first_val)[:50])
        else:
            clean_cols.append(str(col))
    df.columns = clean_cols

    # Check if first row looks like a header
    first_row = df.iloc[0]
    if all(isinstance(v, str) for v in first_row.values if pd.notna(v)):
        potential_headers = [str(v) for v in first_row.values]
        if any(h != c for h, c in zip(potential_headers, df.columns)):
            df.columns = potential_headers
            df = df.iloc[1:].reset_index(drop=True)

    return df


def _df_to_text(df, basename, sheet_name):
    """Convert a DataFrame to compact pipe-separated text."""
    lines = [f"\n--- FILE: {basename} | SHEET: {sheet_name} ({len(df)} rows) ---"]
    header = ' | '.join(str(c) for c in df.columns)
    lines.append(header)
    lines.append('-' * min(len(header), 120))
    for _, row in df.iterrows():
        import pandas as pd
        row_text = ' | '.join(
            str(v)[:80] if pd.notna(v) else ''
            for v in row.values
        )
        lines.append(row_text)
    return '\n'.join(lines)


def _detect_concepts(columns):
    """Infer semantic concepts from column names for reference mapping."""
    joined = ' '.join(str(c).lower() for c in columns)
    concepts = []
    checks = [
        ('activity_id', ('activity id', 'act id', 'id')),
        ('activity_name', ('activity name', 'description', 'task name', 'name')),
        ('predecessor', ('predecessor', 'pred', 'upstream')),
        ('successor', ('successor', 'succ', 'downstream')),
        ('lag', ('lag', 'lead')),
        ('critical_path', ('critical', 'cpm', 'total float', 'float')),
        ('dates', ('start', 'finish', 'date', 'baseline', 'actual')),
        ('duration', ('duration', 'remaining duration')),
        ('progress', ('progress', 'complete', '%', 'weight')),
        ('discipline', ('discipline', 'area', 'wbs', 'phase')),
        ('risk', ('risk', 'issue', 'mitigation')),
        ('deviation', ('deviation', 'variance', 'delta')),
        ('benchmark', ('benchmark', 'productivity', 'norm')),
    ]
    for label, patterns in checks:
        if any(pattern in joined for pattern in patterns):
            concepts.append(label)
    return concepts


def _detect_query_concepts(query):
    """Extract user-intent concepts to keep KB reference hints focused."""
    q = (query or '').lower()
    concepts = []
    concept_checks = [
        ('predecessor', ('predecessor', 'pred', 'dependency', 'dependencies', 'upstream')),
        ('successor', ('successor', 'succ', 'downstream')),
        ('lag', ('lag', 'lead')),
        ('critical_path', ('critical', 'critical path', 'cpm', 'float')),
        ('dates', ('start', 'finish', 'date', 'baseline', 'actual')),
        ('duration', ('duration', 'remaining duration')),
        ('progress', ('progress', 'complete', 's-curve', 'earned value')),
        ('discipline', ('discipline', 'engineering', 'procurement', 'construction', 'commissioning', 'wbs', 'phase')),
        ('risk', ('risk', 'issue', 'mitigation', 'threat')),
        ('deviation', ('deviation', 'variance', 'delay', 'late', 'behind')),
        ('benchmark', ('benchmark', 'productivity', 'compare', 'comparison', 'vs', 'versus')),
    ]
    for label, patterns in concept_checks:
        if any(pattern in q for pattern in patterns):
            concepts.append(label)
    return concepts


def _relationship_types_present(df):
    """Return relationship types detected in predecessor/successor text."""
    rel_types = []
    pred_succ_cols = [c for c in df.columns if any(x in str(c).lower() for x in ['pred', 'succ'])]
    if not pred_succ_cols:
        return rel_types

    combined = ' '.join(
        str(v)
        for col in pred_succ_cols
        for v in df[col].dropna().astype(str).tolist()
    ).upper()
    for rel_type in ['FS', 'SS', 'FF', 'SF']:
        if f':{rel_type}' in combined or f': {rel_type}' in combined or f' {rel_type} ' in combined:
            rel_types.append(rel_type)
    return rel_types


def _build_reference_profile(df, basename, sheet_name):
    """Create a reference-only summary with no raw project rows."""
    columns = [str(c) for c in df.columns]
    concepts = _detect_concepts(columns)
    rel_types = _relationship_types_present(df)

    lines = [
        f"--- FILE: {basename} | SHEET: {sheet_name} ({len(df)} rows) ---",
        "Reference-only profile. Use to interpret equivalent fields in live user data, not to quote values in the answer.",
        f"Columns: {', '.join(columns)}",
    ]

    if concepts:
        lines.append(f"Detected concepts: {', '.join(concepts)}")
    if rel_types:
        lines.append(f"Relationship types present: {', '.join(rel_types)}")

    alias_hints = []
    for col in columns:
        col_lower = col.lower()
        if 'pred' in col_lower:
            alias_hints.append(f"'{col}' behaves like a predecessor field")
        elif 'succ' in col_lower:
            alias_hints.append(f"'{col}' behaves like a successor field")
        elif 'lag' in col_lower or 'lead' in col_lower:
            alias_hints.append(f"'{col}' behaves like a lag/lead field")
        elif 'critical' in col_lower or 'float' in col_lower:
            alias_hints.append(f"'{col}' behaves like a critical-path indicator")
        elif any(term in col_lower for term in ['start', 'finish', 'date']):
            alias_hints.append(f"'{col}' behaves like a schedule date field")
    if alias_hints:
        lines.append("Interpretation hints: " + '; '.join(alias_hints[:8]))

    return '\n'.join(lines), concepts


def _build_large_file_summary(df, basename):
    """
    Build a compact AI-friendly summary for a large file (like
    predecessor-successor-lag data) instead of dumping all rows.
    """
    import pandas as pd
    lines = []
    lines.append(f"\n--- FILE: {basename} (LARGE — {len(df)} rows, summarised) ---")
    lines.append(f"Columns: {', '.join(str(c) for c in df.columns)}")

    # Detect column types by name
    id_cols = [c for c in df.columns if 'id' in c.lower() or 'activity' in c.lower()]
    succ_cols = [c for c in df.columns if 'succ' in c.lower()]
    pred_cols = [c for c in df.columns if 'pred' in c.lower()]
    lag_cols = [c for c in df.columns if 'lag' in c.lower()]
    name_cols = [c for c in df.columns if 'name' in c.lower()]
    date_cols = [c for c in df.columns if any(d in c.lower() for d in ['start', 'finish', 'date'])]

    # Stats
    act_col = id_cols[0] if id_cols else None
    if act_col:
        unique_activities = df[act_col].dropna().nunique()
        lines.append(f"Unique activities: {unique_activities}")

    # Predecessor/Successor relationship summary
    if pred_cols:
        pred_col = pred_cols[0]
        has_pred = df[pred_col].notna().sum()
        lines.append(f"Activities with predecessors: {has_pred}")
        # Count relationship types (FS, SS, FF, SF)
        all_pred_text = ' '.join(df[pred_col].dropna().astype(str).tolist())
        for rel_type in ['FS', 'SS', 'FF', 'SF']:
            count = all_pred_text.count(f': {rel_type}') + all_pred_text.count(f':{rel_type}')
            if count > 0:
                lines.append(f"  {rel_type} relationships in predecessors: ~{count}")

    if succ_cols:
        succ_col = succ_cols[0]
        has_succ = df[succ_col].notna().sum()
        lines.append(f"Activities with successors: {has_succ}")
        all_succ_text = ' '.join(df[succ_col].dropna().astype(str).tolist())
        for rel_type in ['FS', 'SS', 'FF', 'SF']:
            count = all_succ_text.count(f': {rel_type}') + all_succ_text.count(f':{rel_type}')
            if count > 0:
                lines.append(f"  {rel_type} relationships in successors: ~{count}")

    if lag_cols:
        lag_col = lag_cols[0]
        lag_vals = pd.to_numeric(df[lag_col], errors='coerce').dropna()
        if not lag_vals.empty:
            lines.append(f"Lag values: min={lag_vals.min()}, max={lag_vals.max()}, "
                         f"mean={lag_vals.mean():.1f}, median={lag_vals.median()}")
            non_zero_lags = lag_vals[lag_vals != 0]
            if not non_zero_lags.empty:
                lines.append(f"Non-zero lags: {len(non_zero_lags)} activities")

    # Show sample of unique activity name patterns (WBS/phase groups)
    if name_cols:
        name_col = name_cols[0]
        sample_names = df[name_col].dropna().unique()
        # Group by first word or known categories
        categories = {}
        for n in sample_names:
            n_str = str(n).strip()
            if n_str:
                first_word = n_str.split()[0] if n_str.split() else n_str
                categories.setdefault(first_word, 0)
                categories[first_word] += 1
        if categories:
            top_cats = sorted(categories.items(), key=lambda x: x[1], reverse=True)[:15]
            lines.append(f"Activity name categories (top 15): " +
                         ', '.join(f'{k}({v})' for k, v in top_cats))

    # Show first 20 rows as sample
    lines.append(f"\n--- Sample Data (first 20 rows) ---")
    sample_df = df.head(20)
    header = ' | '.join(str(c) for c in sample_df.columns)
    lines.append(header)
    lines.append('-' * min(len(header), 120))
    for _, row in sample_df.iterrows():
        row_text = ' | '.join(
            str(v)[:60] if pd.notna(v) else ''
            for v in row.values
        )
        lines.append(row_text)

    lines.append(f"\n[NOTE: This file has {len(df)} rows. When user asks about specific activities, "
                 f"predecessor/successor chains, or relationship types, relevant rows are dynamically "
                 f"searched and injected below.]")

    return '\n'.join(lines)


def _search_large_file(df, query, max_results=50):
    """
    Search a large DataFrame for rows matching the user's query.
    Returns matching rows as text.
    """
    import pandas as pd
    if df is None or df.empty or not query:
        return ''

    query_lower = query.lower()

    # Extract keywords / activity IDs from query
    # Look for activity ID patterns like A60780, C1011, etc.
    id_patterns = re.findall(r'\b[A-Z]\d{3,6}\b', query, re.IGNORECASE)

    # Look for general keywords
    keywords = [w for w in re.split(r'[\s,;.!?]+', query_lower)
                if len(w) > 3 and w not in {'what', 'show', 'tell', 'about', 'give', 'list',
                                             'this', 'that', 'with', 'from', 'have', 'does',
                                             'which', 'where', 'when', 'will', 'would', 'could',
                                             'should', 'there', 'their', 'they', 'them', 'been',
                                             'being', 'before', 'after', 'between', 'some',
                                             'please', 'help', 'need', 'want', 'like'}]

    from collections import defaultdict
    match_scores = defaultdict(int)

    # Search by Activity ID
    id_col = None
    for c in df.columns:
        if 'activity' in c.lower() and 'id' in c.lower():
            id_col = c
            break

    if id_col and id_patterns:
        for pat in id_patterns:
            mask = df[id_col].astype(str).str.contains(pat, case=False, na=False)
            for idx in df[mask].index.tolist():
                match_scores[idx] += 3  # ID match is high value

    # Search by keywords in all text columns
    text_cols = df.select_dtypes(include=['object']).columns
    for kw in keywords:
        for col in text_cols:
            mask = df[col].astype(str).str.contains(kw, case=False, na=False)
            for idx in df[mask].index.tolist():
                match_scores[idx] += 1

    # Also search predecessor/successor columns for referenced activity IDs
    pred_succ_cols = [c for c in df.columns
                      if any(x in c.lower() for x in ['pred', 'succ'])]
    for kw in keywords + id_patterns:
        for col in pred_succ_cols:
            mask = df[col].astype(str).str.contains(kw, case=False, na=False)
            for idx in df[mask].index.tolist():
                match_scores[idx] += 1

    if not match_scores:
        return ''

    # Limit results by highest match count
    sorted_indices = sorted(match_scores.keys(), key=lambda idx: match_scores[idx], reverse=True)
    best_indices = sorted_indices[:max_results]
    matched_df = df.loc[best_indices]

    lines = [f"\n--- QUERY-MATCHED ROWS from predecessor/successor data ({len(matched_df)} matches) ---"]
    header = ' | '.join(str(c) for c in matched_df.columns)
    lines.append(header)
    lines.append('-' * min(len(header), 120))
    for _, row in matched_df.iterrows():
        row_text = ' | '.join(
            str(v)[:80] if pd.notna(v) else ''
            for v in row.values
        )
        lines.append(row_text)

    return '\n'.join(lines)


def _read_excel_to_text(filepath):
    """
    Read an Excel / CSV file and convert every sheet into a compact text block.
    Returns (text_representation, total_row_count, combined_df_or_None,
    is_large, concepts).
    Text is always a reference-only summary; raw KB rows are never injected
    into AI prompts.
    """
    import pandas as pd

    ext = os.path.splitext(filepath)[1].lower()
    basename = os.path.basename(filepath)
    lines = []
    total_rows = 0
    combined_df = None
    is_large = False
    concepts = set()

    try:
        if ext == '.csv':
            df = pd.read_csv(filepath)
            sheets = {'Sheet1': df}
        else:
            sheets = pd.read_excel(filepath, sheet_name=None, engine='openpyxl')

        all_dfs = []
        for sheet_name, df in sheets.items():
            df = _clean_dataframe(df)
            if df.empty:
                continue
            total_rows += len(df)
            all_dfs.append(df)

        if total_rows > MAX_INLINE_ROWS:
            is_large = True

        if all_dfs:
            combined_df = pd.concat(all_dfs, ignore_index=True)

        for sheet_name, df in sheets.items():
            df = _clean_dataframe(df)
            if df.empty:
                continue
            profile_text, sheet_concepts = _build_reference_profile(df, basename, sheet_name)
            lines.append(profile_text)
            concepts.update(sheet_concepts)

    except Exception as e:
        lines.append(f"[ERROR reading {basename}: {str(e)}]")

    return '\n'.join(lines), total_rows, combined_df, is_large, sorted(concepts)


def reload_kb_files():
    """
    (Re)load all Excel/CSV files from the Knowledgebase/ folder.
    Only re-reads files whose content has actually changed (by MD5 hash).
    """
    global _kb_cache, _last_loaded

    if not os.path.isdir(KB_FOLDER):
        print(f"[KB LOADER] Knowledgebase folder not found: {KB_FOLDER}")
        return

    patterns = ['*.xlsx', '*.xls', '*.csv']
    found_files = []
    for pat in patterns:
        found_files.extend(glob.glob(os.path.join(KB_FOLDER, pat)))

    if not found_files:
        print("[KB LOADER] No Excel/CSV files found in Knowledgebase/")
        return

    loaded = 0
    skipped = 0
    for fpath in found_files:
        fname = os.path.basename(fpath)
        fhash = _file_hash(fpath)

        # Skip if already cached with same hash
        if fname in _kb_cache and _kb_cache[fname].get('hash') == fhash:
            skipped += 1
            continue

        text, rows, combined_df, is_large, concepts = _read_excel_to_text(fpath)
        _kb_cache[fname] = {
            'text': text,
            'rows': rows,
            'hash': fhash,
            'path': fpath,
            'loaded_at': datetime.now().isoformat(),
            'df': combined_df,       # Only set for large files
            'is_large': is_large,
            'concepts': concepts,
        }
        loaded += 1
        tag = f"(LARGE — {rows} rows, summary + search)" if is_large else f"({rows} rows, inline)"
        print(f"[KB LOADER] Loaded: {fname} {tag}")

    # Remove cache entries for files that no longer exist
    current_names = {os.path.basename(f) for f in found_files}
    removed = [k for k in _kb_cache if k not in current_names]
    for k in removed:
        del _kb_cache[k]

    _last_loaded = datetime.now().isoformat()
    print(f"[KB LOADER] Done — {loaded} loaded, {skipped} unchanged, {len(removed)} removed. "
          f"Total KB files: {len(_kb_cache)}")


def get_kb_context(user_query='', include_explicit_matches=False, explicit_match_limit=25):
    """
    Return a formatted string with KB reference profiles, ready to inject into
    the AI prompt.

    Modes:
    - include_explicit_matches=False (default): reference-only profile for
      behavioral mapping and terminology.
    - include_explicit_matches=True: includes query-matched KB rows as
      explicit evidence candidates in addition to reference profile.

    Returns empty string if no KB files are loaded.
    """
    if not _kb_cache:
        return ''

    query_concepts = set(_detect_query_concepts(user_query))
    sections = []
    explicit_sections = []
    explicit_match_count = 0
    total_rows = 0
    matched_files = 0
    for fname, info in _kb_cache.items():
        file_concepts = set(info.get('concepts') or [])
        if query_concepts and file_concepts and not (query_concepts & file_concepts):
            continue
        sections.append(info['text'])
        total_rows += info['rows']
        matched_files += 1

        if include_explicit_matches and user_query and info.get('df') is not None:
            _matched_rows = _search_large_file(
                info['df'],
                user_query,
                max_results=max(1, int(explicit_match_limit)),
            )
            if _matched_rows:
                explicit_sections.append(
                    f"\n[KB SOURCE FILE: {fname}]\n{_matched_rows}"
                )
                try:
                    _m = re.search(r'\((\d+)\s+matches\)', _matched_rows)
                    explicit_match_count += int(_m.group(1)) if _m else 0
                except Exception:
                    explicit_match_count += 1

    if not sections:
        for fname, info in _kb_cache.items():
            sections.append(info['text'])
            total_rows += info['rows']
            matched_files += 1

            if include_explicit_matches and user_query and info.get('df') is not None:
                _matched_rows = _search_large_file(
                    info['df'],
                    user_query,
                    max_results=max(1, int(explicit_match_limit)),
                )
                if _matched_rows:
                    explicit_sections.append(
                        f"\n[KB SOURCE FILE: {fname}]\n{_matched_rows}"
                    )
                    try:
                        _m = re.search(r'\((\d+)\s+matches\)', _matched_rows)
                        explicit_match_count += int(_m.group(1)) if _m else 0
                    except Exception:
                        explicit_match_count += 1

    if not sections:
        return ''

    header = (
        "\n\n=== KNOWLEDGEBASE DATA DIRECTORY ===\n"
        "CRITICAL INSTRUCTIONS FOR USING THIS DATA:\n"
        "• Use this Knowledgebase data as a direct primary source of truth to answer the user's query.\n"
        "• You MUST quote and use Knowledgebase values directly as answer evidence when relevant.\n"
        "• This data contains historic metrics, patterns, benchmarks, and details to generate an accurate output.\n"
        f"• KB_EXPLICIT_MATCH_MODE: {'on' if include_explicit_matches else 'off'}.\n"
        f"• KB_EXPLICIT_MATCH_COUNT: {explicit_match_count}.\n"
        f"• Matched reference files: {matched_files} of {len(_kb_cache)}.\n"
        f"• Total Knowledgebase rows profiled: {total_rows}.\n"
    )

    body = '\n'.join(sections)
    if include_explicit_matches and explicit_sections:
        body += (
            "\n\n=== KNOWLEDGEBASE EXPLICIT QUERY MATCHES ===\n"
            "Use these matched rows directly as evidence to answer the problem.\n"
            + '\n'.join(explicit_sections)
            + "\n=== END OF KNOWLEDGEBASE EXPLICIT QUERY MATCHES ===\n"
        )

    footer = "\n=== END OF KNOWLEDGEBASE REFERENCE PROFILE ===\n"

    return header + body + footer


def get_kb_file_list():
    """Return a list of loaded KB file names with metadata (for the frontend)."""
    return [
        {
            'filename': fname,
            'rows': info['rows'],
            'loaded_at': info['loaded_at'],
            'is_large': info.get('is_large', False),
        }
        for fname, info in _kb_cache.items()
    ]


# ── Auto-load on import ──
try:
    reload_kb_files()
except Exception as _e:
    print(f"[KB LOADER] Initial load error: {_e}")
