"""
Top Models — internal localhost inspector (read-only over pipeline outputs).

Run: streamlit run dashboard.py
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out"
CONFIG_PATH = ROOT / "config.yaml"

SIGNALS = ["search", "listings", "community", "first_party", "problems"]
SCORE_COLS = {s: f"score_{s}" for s in SIGNALS}

RAW_SIGNAL_HINTS: dict[str, list[str]] = {
    "search": ["signal_search", "signal_report_rank_score"],
    "listings": ["signal_listing_volume", "signal_market_days_supply"],
    "community": ["signal_reddit_mentions"],
    "first_party": ["signal_first_party", "signal_first_party_scans", "signal_first_party_avg_score"],
    "problems": [
        "signal_problems",
        "signal_recall_count",
        "signal_complaint_count",
        "signal_investigation_count",
    ],
}


def _mtime(path: Path) -> float:
    return path.stat().st_mtime if path.exists() else 0.0


@st.cache_data
def load_config(config_mtime: float) -> dict:
    if not CONFIG_PATH.exists():
        return {}
    with CONFIG_PATH.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@st.cache_data
def load_pipeline_output(json_mtime: float, backlog_mtime: float) -> dict:
    json_path = OUT / "top_models.json"
    if not json_path.exists():
        return {"models": pd.DataFrame(), "enrichment": {}, "backlog": {}, "generated_at": None}

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    models = pd.DataFrame(payload.get("models") or [])
    enrichment = {
        row["canonical_id"]: row.get("enrichment")
        for row in payload.get("enrichment") or []
        if row.get("canonical_id")
    }
    backlog = parse_backlog((OUT / "backlog.md").read_text(encoding="utf-8")) if (OUT / "backlog.md").exists() else {}
    return {
        "models": models,
        "enrichment": enrichment,
        "backlog": backlog,
        "generated_at": payload.get("generated_at"),
    }


def parse_backlog(text: str) -> dict[str, str]:
    """Map canonical-ish keys to markdown brief sections."""
    sections: dict[str, str] = {}
    for block in re.split(r"\n##\s+", text):
        if not block.strip():
            continue
        lines = block.strip().split("\n")
        header = lines[0].strip()
        # "1. 2014 HONDA ACCORD" → year/make/model key
        m = re.match(r"\d+\.\s+(\d{4})\s+(.+)", header)
        if m:
            year, rest = m.group(1), m.group(2).strip()
            parts = rest.split()
            if len(parts) >= 2:
                make, model = parts[0], " ".join(parts[1:])
                key = f"{year}|{make.upper()}|{model.upper()}"
                sections[key] = "\n".join(lines[1:]).strip()
        sections[header] = "\n".join(lines[1:]).strip()
    return sections


def model_label(row: pd.Series) -> str:
    return f"{int(row['year'])} {row['make']} {row['model']}"


def priority_preview(df: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    total = pd.Series(0.0, index=df.index)
    for signal, col in SCORE_COLS.items():
        if col in df.columns:
            total = total + df[col].fillna(0) * weights.get(signal, 0.0)
    return total


def signal_present(row: pd.Series, signal: str) -> bool:
    col = SCORE_COLS[signal]
    if col in row and pd.notna(row[col]) and float(row[col]) > 0:
        return True
    for raw in RAW_SIGNAL_HINTS.get(signal, []):
        if raw in row and pd.notna(row[raw]) and float(row[raw]) > 0:
            return True
    return False


def save_weights(weights: dict[str, float]) -> None:
    with CONFIG_PATH.open(encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    cfg.setdefault("weights", {})
    for key in SIGNALS:
        cfg["weights"][key] = round(float(weights[key]), 4)
    with CONFIG_PATH.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(cfg, fh, default_flow_style=False, sort_keys=False)


def render_enrichment(enrichment: dict | None) -> None:
    if not enrichment:
        st.info("No enrichment data for this model.")
        return

    rc = enrichment.get("recall_count")
    if rc:
        st.markdown(f"**Recalls:** {rc.get('value')} — _{rc.get('source')}_")

    inv = enrichment.get("investigation_count")
    if inv:
        st.markdown(f"**Investigations:** {inv.get('value')} — _{inv.get('source')}_")

    fp = enrichment.get("first_party_scan_count")
    if fp:
        st.markdown(f"**MotoMetrics scans:** {fp.get('value')} — _{fp.get('source')}_")

    fps = enrichment.get("first_party_avg_score")
    if fps:
        st.markdown(f"**Avg scan score:** {fps.get('value')} — _{fps.get('source')}_")

    complaints = enrichment.get("top_complaint_components") or []
    if complaints:
        st.markdown("**Top complaint categories**")
        for item in complaints:
            val = item.get("value") or {}
            comp = val.get("component", val) if isinstance(val, dict) else val
            count = val.get("count", "") if isinstance(val, dict) else ""
            review = " ⚠️ _needs review_" if item.get("needs_review") else ""
            st.markdown(f"- {comp} ({count}) — _{item.get('source')}_{review}")

    recalls = enrichment.get("recalls") or []
    if recalls:
        st.markdown("**Recall details (sample)**")
        for r in recalls[:5]:
            st.markdown(
                f"- **{r.get('component', 'Recall')}** — {r.get('summary', '')[:220]}… "
                f"_(source: {r.get('source', 'NHTSA Recalls API')})_"
            )

    for note in enrichment.get("notes") or []:
        st.caption(note)


def coverage_summary(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for signal in SIGNALS:
        has = int(df.apply(lambda r: signal_present(r, signal), axis=1).sum()) if len(df) else 0
        rows.append({"signal": signal, "models_with_data": has, "models_missing": len(df) - has})
    return pd.DataFrame(rows)


def coverage_matrix(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    matrix = []
    for _, row in df.iterrows():
        entry = {"model": model_label(row), "canonical_id": row.get("canonical_id", "")}
        for signal in SIGNALS:
            entry[signal] = "✓" if signal_present(row, signal) else "—"
        matrix.append(entry)
    return pd.DataFrame(matrix)


# --- UI ---

st.set_page_config(page_title="Top Models (internal)", layout="wide", page_icon="🚗")

json_mtime = _mtime(OUT / "top_models.json")
backlog_mtime = _mtime(OUT / "backlog.md")
config_mtime = _mtime(CONFIG_PATH)

cfg = load_config(config_mtime)
data = load_pipeline_output(json_mtime, backlog_mtime)
df: pd.DataFrame = data["models"]
enrichment_map: dict = data["enrichment"]
backlog_map: dict = data["backlog"]

st.title("Top Models — pipeline inspector")
st.caption("Read-only viewer over `out/` — re-run `topmodels run --phase 1` to refresh data.")

if df.empty:
    st.error("No `out/top_models.json` found. Run the pipeline first: `topmodels run --phase 1`")
    st.stop()

# Header / run summary
sources = (cfg.get("sources") or {}) if cfg else {}
enabled_sources = [k for k, v in sources.items() if v]
generated = data.get("generated_at")
if generated:
    try:
        run_dt = datetime.fromisoformat(generated.replace("Z", "+00:00"))
        run_str = run_dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        run_str = generated
else:
    run_str = datetime.fromtimestamp(json_mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

cov = coverage_summary(df)
cov_line = " · ".join(f"{r['signal']}: {r['models_with_data']}/{len(df)}" for _, r in cov.iterrows())

col1, col2, col3, col4 = st.columns(4)
col1.metric("Models ranked", len(df))
col2.metric("Last run", run_str)
col3.metric("Sources on", len(enabled_sources))
col4.metric("Output file", "top_models.json")

st.markdown(f"**Enabled sources:** {', '.join(enabled_sources) or '—'}")
st.markdown(f"**Signal coverage:** {cov_line}")

# Sidebar — live weight preview
st.sidebar.header("Weights (preview only)")
default_weights = (cfg.get("weights") or {}) if cfg else {}
w: dict[str, float] = {}
for signal in SIGNALS:
    w[signal] = st.sidebar.slider(
        signal,
        min_value=0.0,
        max_value=1.0,
        value=float(default_weights.get(signal, 0.2)),
        step=0.05,
    )

df = df.copy()
df["priority_preview"] = priority_preview(df, w)
df = df.sort_values("priority_preview", ascending=False).reset_index(drop=True)
df["preview_rank"] = range(1, len(df) + 1)

if st.sidebar.button("Save weights to config.yaml", type="primary"):
    save_weights(w)
    st.sidebar.success("Saved to config.yaml — re-run pipeline to apply.")
    st.cache_data.clear()

st.sidebar.caption("Slider changes re-rank instantly. Saving updates config only on button click.")

tab_ranked, tab_drill, tab_coverage, tab_risers, tab_backlog = st.tabs(
    ["Ranked table", "Model drill-down", "Coverage & gaps", "Risers", "Backlog"]
)

with tab_ranked:
    display_cols = [
        "preview_rank",
        "year",
        "make",
        "model",
        "priority_preview",
        "priority_score",
        "explanation",
        *[SCORE_COLS[s] for s in SIGNALS if SCORE_COLS[s] in df.columns],
        "riser",
    ]
    display_cols = [c for c in display_cols if c in df.columns]
    st.dataframe(
        df[display_cols].rename(
            columns={
                "preview_rank": "rank (preview)",
                "priority_preview": "score (preview)",
                "priority_score": "score (pipeline)",
                **{SCORE_COLS[s]: f"norm_{s}" for s in SIGNALS},
            }
        ),
        width="stretch",
        hide_index=True,
    )

with tab_drill:
    labels = df.apply(model_label, axis=1).tolist()
    choice = st.selectbox("Inspect a model", labels)
    row = df.loc[df.apply(model_label, axis=1) == choice].iloc[0]
    cid = row.get("canonical_id", "")

    st.subheader(choice)
    st.caption(row.get("explanation", ""))

    st.markdown("**Why it ranks (normalized components)**")
    chart_data = {s: float(row.get(SCORE_COLS[s], 0) or 0) for s in SIGNALS}
    st.bar_chart(chart_data)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Preview score**")
        st.metric("priority_preview", f"{row['priority_preview']:.4f}")
        st.markdown("**Pipeline score**")
        st.metric("priority_score", f"{row.get('priority_score', 0):.4f}")
    with c2:
        st.markdown("**Raw signals**")
        raw_cols = sorted(c for c in df.columns if c.startswith("signal_"))
        raw = {c.replace("signal_", ""): row[c] for c in raw_cols if pd.notna(row.get(c))}
        st.json(raw)

    st.subheader("Enrichment (sourced)")
    render_enrichment(enrichment_map.get(cid))

    st.subheader("Content brief")
    brief = backlog_map.get(cid) or backlog_map.get(f"{int(row['year'])} {row['make']} {row['model']}", "")
    if brief:
        st.markdown(brief)
    else:
        st.info("No brief section matched — see Backlog tab for full file.")

with tab_coverage:
    st.subheader("Coverage by signal")
    st.dataframe(cov, width="stretch", hide_index=True)

    st.subheader("Per-model gaps")
    matrix = coverage_matrix(df)
    st.dataframe(matrix, width="stretch", hide_index=True)

    missing = matrix.apply(
        lambda r: [s for s in SIGNALS if r.get(s) == "—"],
        axis=1,
    )
    thin = df.copy()
    thin["missing_signals"] = missing
    thin["gap_count"] = thin["missing_signals"].apply(len)
    thin = thin.sort_values("gap_count", ascending=False)
    if len(thin):
        st.markdown("**Thinnest data (most missing signals)**")
        st.dataframe(
            thin[["year", "make", "model", "gap_count", "missing_signals"]].head(15),
            width="stretch",
            hide_index=True,
        )

    st.caption(
        "Freshness: enrichment `as_of` timestamps are stored in top_models.json enrichment blocks. "
        "Re-run pipeline to refresh NHTSA/Trends data."
    )

with tab_risers:
    if "riser" in df.columns and "previous_rank" in df.columns:
        risers = df[df["riser"] == True].copy()  # noqa: E712
        if risers.empty:
            st.info("No risers vs the previous run (or first run — no prior top_models.json to diff).")
        else:
            st.dataframe(
                risers[
                    [
                        "year",
                        "make",
                        "model",
                        "rank",
                        "previous_rank",
                        "priority_score",
                        "explanation",
                    ]
                ],
                width="stretch",
                hide_index=True,
            )
    else:
        st.info("Riser fields not present in output — run pipeline twice to populate diffs.")

    st.subheader("Biggest rank moves (preview vs pipeline)")
    if "rank" in df.columns:
        moves = df.copy()
        moves["rank_delta"] = moves["rank"] - moves["preview_rank"]
        moves = moves.sort_values("rank_delta")
        st.caption("Negative delta = climbed under preview weights")
        st.dataframe(
            moves[["year", "make", "model", "rank", "preview_rank", "rank_delta", "priority_preview"]].head(10),
            width="stretch",
            hide_index=True,
        )

with tab_backlog:
    backlog_path = OUT / "backlog.md"
    if backlog_path.exists():
        st.markdown(backlog_path.read_text(encoding="utf-8"))
    else:
        st.warning("out/backlog.md not found.")
