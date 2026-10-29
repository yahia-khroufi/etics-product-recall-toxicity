import sys
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import altair as alt
import pandas as pd
import streamlit as st

from dashboard.data import make_engine, read_store, read_detail, read_observations, read_dimensions, read_live_signature
from dashboard.intake import render_intake
from dashboard.logic import (STATUSES, QUESTIONS, filter_history, latest_per_case,
                             scored_cohort, presentation_table, csv_bytes, quote_diagnostics)

st.set_page_config(page_title="ETICS · Observatoire des rappels", page_icon="◈", layout="wide")
st.markdown("""<style>
.block-container {max-width: 1500px; padding-top: 2.4rem; padding-bottom: 3rem;}
[data-testid="stSidebar"] {border-right: 1px solid #e2e8f0;}
[data-testid="stMetric"] {background:white; border:1px solid #e2e8f0; border-radius:14px; padding:18px 20px;}
[data-testid="stMetricValue"] {font-weight:650; color:#112b3c;}
h1 {letter-spacing:-.045em; font-weight:750!important; font-size:clamp(1.6rem,3vw,2.6rem)!important;}
h2,h3 {color:#173747; letter-spacing:-.02em;}
.eyebrow {font-size:12px; letter-spacing:.15em; color:#0f766e; font-weight:700; margin-bottom:10px;}
.intro {font-size:17px; color:#64748b; max-width:850px; margin-top:-6px; margin-bottom:28px;}
.brand {font-size:30px; font-weight:800; letter-spacing:.12em; color:#153547;}
@media (max-width:640px) {
 .block-container {padding:1.5rem 1rem;}
 .intro {font-size:15px; margin-bottom:18px;}
 .eyebrow {font-size:10px; letter-spacing:.08em;}
}
</style>""", unsafe_allow_html=True)


@st.cache_resource
def database():
    return make_engine()


@st.cache_data(ttl=30, show_spinner=False)
def store():
    return read_store(database())


@st.fragment(run_every="10s")
def refresh_when_warehouse_changes():
    try:
        signature = read_live_signature(database())
    except Exception:
        return
    previous = st.session_state.get("warehouse_signature")
    st.session_state["warehouse_signature"] = signature
    if previous is not None and previous != signature:
        store.clear()
        st.rerun()


def table(frame, **kwargs):
    st.dataframe(frame, hide_index=True, width="stretch", **kwargs)


def results_table(frame, key):
    shown = presentation_table(frame)
    table(shown, column_config={
        "Indice / 100": st.column_config.NumberColumn(format="%.1f"),
        "Complétude %": st.column_config.NumberColumn(format="%.0f"),
    })
    st.download_button("Exporter la sélection en CSV", csv_bytes(shown),
                       file_name=f"etics_{key}.csv", mime="text/csv", key=f"export_{key}")


def choose_analysis(frame, key):
    indexed = frame.set_index("source_result_id", drop=False)
    ids = indexed.sort_values(["case_id", "analysis_created_at"], ascending=[True, False]).index.tolist()
    selected = st.selectbox("Analyse à consulter", ids, key=key, format_func=lambda value:
        f"{indexed.loc[value, 'case_id']} · #{value} · {indexed.loc[value, 'product_name'] or 'Produit non renseigné'} · "
        f"{STATUSES.get(indexed.loc[value, 'analysis_status'], indexed.loc[value, 'analysis_status'])}")
    return indexed.loc[selected]


def detail(row):
    try:
        metadata, documents = read_detail(database(), row.source_result_id)
    except Exception:
        st.error("Le détail n'est pas accessible. Vérifiez PostgreSQL puis actualisez.")
        return
    st.subheader(f"Rappel {row.case_id} · analyse #{row.source_result_id}")
    st.write(row.product_name or "Produit non renseigné")
    st.caption(f"Entreprise : {row.company_name or 'Non renseignée'} · Marque : {row.brand or 'Non renseignée'}")
    a, b, c = st.columns(3)
    a.metric("Statut", STATUSES.get(row.analysis_status, row.analysis_status))
    valid = row.analysis_status == "scored" and bool(row.evidence_verified)
    a_label = "Indice de problèmes / 100" if valid else "Indice enregistré / 100"
    b.metric(a_label, f"{row.toxicity_score:.1f}" if pd.notna(row.toxicity_score) else "—")
    c.metric("Complétude Q1–Q5", f"{row.completeness_score * 100:.0f} %" if pd.notna(row.completeness_score) else "—")
    if row.analysis_status == "needs_review":
        st.warning("Citations à revoir : aucun score ni verdict n'a été attribué à cette analyse.")
    if row.analysis_status == "legacy_unlinked":
        st.warning("Analyse ancienne conservée. Les sources utilisées ne sont pas liées et les règles historiques sont inconnues ; son score ne participe pas aux indicateurs comparables.")
    applied_rules = row.scoring_version if valid else "non appliquées ou inconnues"
    st.caption(f"Modèle : {row.model_name} · Prompt : {row.prompt_version} · Règles appliquées : {applied_rules}")
    st.write("Verdict enregistré :", row.verdict if pd.notna(row.verdict) else "Non attribué")
    questions = pd.DataFrame([{"Question": key.upper(), "Critère": label,
                               "Réponse du LLM": "Oui" if row[key] else "Non"}
                              for key, label in QUESTIONS.items()])
    table(questions)
    st.caption("Q1–Q5 : présence d'informations. Q6 : ambiguïté. Q7 : minimisation. Une réponse Oui n'a donc pas la même interprétation pour tous les critères.")
    verified, quotes, reasons = quote_diagnostics(row, documents)
    with st.expander("Citations et contrôle des sources", expanded=row.analysis_status == "needs_review"):
        st.caption("Contrôle lexical : casse et espaces normalisés. Il ne valide pas la justesse de l'interprétation du LLM.")
        if verified is None:
            st.info(reasons[0])
        else:
            st.write("Contrôle recalculé sur les versions liées :", "Conforme" if verified else "À revoir")
            if bool(row.evidence_verified) != verified:
                st.warning("Le contrôle recalculé diffère du statut enregistré. Aucune donnée n'a été modifiée.")
            for reason in reasons:
                st.write("• " + reason)
        if quotes:
            table(pd.DataFrame(quotes))
        elif row.evidence:
            st.json(row.evidence)
        else:
            st.caption("Aucune citation enregistrée.")
    st.subheader("Documents utilisés par cette analyse")
    if not documents:
        st.info("Aucun document lié à cette analyse. Les archives actuelles ne permettent pas de reconstruire ses entrées historiques.")
    else:
        for column, role, label in zip(st.columns(2), ("official", "company"), ("Fiche officielle", "Communication de l'entreprise")):
            with column:
                st.markdown(f"**{label}**")
                doc = documents.get(role)
                if not doc:
                    st.info("Version non liée.")
                    continue
                st.caption(f"Version {doc['version_number']} · identifiant {doc['id']} · observée le {doc['observed_at']:%d/%m/%Y %H:%M %Z}")
                st.text_area(label, doc["raw_text"], height=290, disabled=True,
                             key=f"doc_{row.source_result_id}_{role}", label_visibility="collapsed")
                url = doc.get("source_url") or ""
                if urlparse(url).scheme in ("https", "http"):
                    st.link_button("Ouvrir la source", url)
                with st.expander("Provenance du document"):
                    st.json({key: doc.get(key) for key in ("source_url", "source_path", "text_sha256", "raw_sha256", "provenance")})
    with st.expander("Traçabilité de l'exécution"):
        st.json({"analyse": int(row.source_result_id), "execution": metadata["run_id"],
                 "modele_demande": row.model_name, "revision_declaree": metadata["model_revision"],
                 "prompt": row.prompt_version, "prompt_sha256": metadata["prompt_hash"],
                 "scoring": row.scoring_version, "git_commit": metadata["git_commit"],
                 "scoring_prevu_par_execution": metadata.get("planned_scoring_version"),
                 "git_dirty": metadata["git_dirty"], "debut": metadata["started_at"],
                 "fin": metadata["finished_at"], "contexte_dwh": row.context_origin,
                 "snapshot_observe_le": metadata["snapshot_observed_at"]})
        if row.explanation:
            st.text(row.explanation)


st.sidebar.markdown('<div class="brand">ETICS</div>', unsafe_allow_html=True)
st.sidebar.caption("OBSERVATOIRE DES RAPPELS")
page = st.sidebar.radio("Navigation", ["Vue d'ensemble", "Nouveau rappel", "Rappels", "À revoir", "Historique", "Traçabilité"],
                        label_visibility="collapsed", key="page")
st.sidebar.divider()
if st.sidebar.button("Actualiser les données", width="stretch"):
    store.clear()

try:
    history, counts, etl = store()
except Exception:
    st.title("ETICS · Observatoire des rappels")
    st.error("L'entrepôt PostgreSQL n'est pas accessible ou les migrations DWH sont absentes.")
    st.info("Démarrez le service Docker db et vérifiez DATABASE_URL dans le fichier .env du projet. Aucun jeu de démonstration n'est affiché.")
    st.stop()

st.sidebar.caption(f"Lecture PostgreSQL · {counts['read_at'].astimezone(ZoneInfo('Africa/Casablanca')):%d/%m/%Y %H:%M:%S} (Casablanca)")
st.sidebar.caption("Cache de lecture : 30 secondes")
if page == "Nouveau rappel":
    render_intake(database())
    st.stop()
refresh_when_warehouse_changes()
if history.empty:
    st.title("ETICS · Observatoire des rappels")
    st.info("L'entrepôt est vide. Exécutez python -m src.dwh.etl après le pipeline.")
    st.stop()

search = st.sidebar.text_input("Rechercher", placeholder="Entreprise, produit, rappel…", key="search")
choices = {}
with st.sidebar.expander("Filtres d'analyse"):
    for col, label in (("company_name", "Entreprise"), ("model_name", "Modèle"),
                       ("prompt_version", "Prompt"), ("scoring_version", "Règles de scoring"),
                       ("analysis_status", "Statut")):
        options = sorted(history[col].fillna("Non renseigné").unique().tolist())
        choices[col] = st.multiselect(label, options, key=f"filter_{col}",
            format_func=(lambda v: STATUSES.get(v, v)) if col == "analysis_status" else str)
    use_period = st.checkbox("Limiter la période d'analyse", key="use_period")
    dates = history.analysis_created_at.dt.tz_convert("Africa/Casablanca").dt.date
    period = st.date_input("Dates d'analyse (Casablanca)", value=(dates.min(), dates.max()),
                           disabled=not use_period, key="period")
    if use_period and len(period) != 2:
        st.info("Sélectionnez les deux bornes de la période.")
        st.stop()
filtered = filter_history(history, search=search, choices=choices,
                          date_range=period if use_period else None)
all_analyses = st.sidebar.toggle("Toutes les analyses", value=False, key="all_analyses")
selected = filtered if all_analyses else latest_per_case(filtered)
st.sidebar.caption("Dernière analyse par rappel dans la sélection." if not all_analyses else "Toutes les analyses de la sélection.")
st.sidebar.divider()
st.sidebar.caption("Consultation SQL en lecture seule.\n\nLes nouveaux dépôts sont analysés par le processus ETICS en arrière-plan.")

st.markdown('<div class="eyebrow">COMMUNICATION · TRANSPARENCE · TRAÇABILITÉ</div>', unsafe_allow_html=True)
st.title(page if page != "Vue d'ensemble" else "La transparence des rappels")
st.markdown('<div class="intro">Explorer les résultats ETICS, retrouver leurs preuves et suivre les analyses dans le temps.</div>', unsafe_allow_html=True)
st.caption(f"Corpus : {counts['recalls']} rappels · {counts['source_results']} analyses sources · {counts['facts']} faits DWH · {counts['document_versions']} versions documentaires")
if counts["source_results"] != counts["facts"]:
    st.warning("Le nombre de faits DWH diffère des analyses sources. L'entrepôt nécessite une vérification ETL.")
if selected.empty:
    st.info("Aucune analyse ne correspond aux filtres. Modifiez la recherche ou les filtres dans le panneau de gauche.")
    st.stop()

cohort = scored_cohort(selected)
protocol_columns = [c for c in ("model_name", "provider_reported_revision", "prompt_version", "scoring_version") if c in cohort]
single_protocol = len(cohort[protocol_columns].drop_duplicates()) == 1
st.caption(f"Sélection : {selected.case_id.nunique()} rappels · {len(selected)} analyses · {len(cohort)} avec score et citations vérifiées")

if page == "Vue d'ensemble":
    cols = st.columns(4)
    cols[0].metric("Rappels sélectionnés", selected.case_id.nunique())
    cols[1].metric("Analyses scorées", len(cohort))
    cols[2].metric("Citations à revoir", int(selected.analysis_status.eq("needs_review").sum()))
    cols[3].metric("Indice moyen / 100", f"{cohort.toxicity_score.mean():.1f}" if single_protocol else "—")
    st.caption("L'indice mesure les problèmes de communication selon les règles ETICS. Il ne mesure ni la toxicité chimique ni une probabilité de danger. Les résultats à revoir et les analyses anciennes non liées sont exclus de la moyenne.")
    if not cohort.empty and not single_protocol:
        st.info("Plusieurs protocoles sont sélectionnés : aucune moyenne globale n'est calculée. Consultez les moyennes par protocole ci-dessous.")
        aggregates = cohort.groupby(protocol_columns, dropna=False).toxicity_score.agg(["count", "mean"]).reset_index()
        table(aggregates.rename(columns={"count": "Analyses", "mean": "Indice moyen / 100"}))
    left, right = st.columns([1, 1.5])
    with left:
        st.subheader("État des analyses")
        states = selected.analysis_status.map(STATUSES).fillna(selected.analysis_status).value_counts().rename_axis("Statut").reset_index(name="Analyses")
        chart = alt.Chart(states).mark_bar(cornerRadiusEnd=5, size=32).encode(
            x=alt.X("Analyses:Q", title=None, axis=alt.Axis(tickMinStep=1)),
            y=alt.Y("Statut:N", title=None, sort="-x"),
            color=alt.Color("Statut:N", scale=alt.Scale(domain=list(STATUSES.values()),
                range=["#0f766e", "#d49a32", "#64748b", "#94a3b8"]), legend=None),
            tooltip=["Statut", "Analyses"])
        st.altair_chart(chart.properties(height=235), width="stretch")
    with right:
        st.subheader("Distribution de l'indice")
        if cohort.empty:
            st.info("Aucune analyse comparable avec score dans cette sélection.")
        else:
            chart = alt.Chart(cohort).mark_bar(color="#0f766e", cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
                x=alt.X("toxicity_score:Q", bin=alt.Bin(step=10, extent=[0, 100]), title="Indice de problèmes / 100"),
                y=alt.Y("count():Q", title="Analyses", axis=alt.Axis(tickMinStep=1)),
                tooltip=[alt.Tooltip("count():Q", title="Analyses")])
            st.altair_chart(chart.properties(height=235), width="stretch")
    st.subheader("Résultats de la sélection")
    results_table(selected, "selection")

elif page == "Rappels":
    detail(choose_analysis(selected, "detail_id"))

elif page == "À revoir":
    review = selected[selected.analysis_status.eq("needs_review")]
    st.write("Ces réponses sont conservées avec leurs citations. Le contrôle de présence a échoué ; elles n'ont pas été scorées.")
    if review.empty:
        st.success("Aucune citation à revoir dans cette sélection.")
    else:
        results_table(review, "revue")
        detail(choose_analysis(review, "review_id"))

elif page == "Historique":
    st.caption("Cette vue utilise toutes les analyses correspondant aux filtres, même si le mode Dernière analyse est activé.")
    cases = sorted(filtered.case_id.unique())
    case_id = st.selectbox("Rappel à suivre", cases, key="history_case")
    case_history = filtered[filtered.case_id.eq(case_id)].sort_values(["analysis_created_at", "source_result_id"])
    st.subheader("Historique des analyses LLM")
    results_table(case_history, "historique")
    comparable = scored_cohort(case_history).copy()
    if len(comparable) > 0:
        comparable["Protocole"] = comparable.model_name + " · " + comparable.prompt_version + " · " + comparable.scoring_version
        comparable["Date locale"] = comparable.analysis_created_at.dt.tz_convert("Africa/Casablanca").dt.strftime("%d/%m/%Y %H:%M:%S")
        st.altair_chart(alt.Chart(comparable).mark_circle(size=120).encode(
            x=alt.X("analysis_created_at:T", title="Date d'analyse (UTC)"),
            y=alt.Y("toxicity_score:Q", scale=alt.Scale(domain=[0, 100]), title="Indice / 100"),
            color=alt.Color("Protocole:N", legend=None),
            tooltip=["source_result_id", "Date locale", "toxicity_score", "Protocole"]
        ).properties(height=240), width="stretch")
    st.info("Des analyses successives peuvent utiliser des modèles, prompts ou règles différents. Elles ne prouvent pas qu'une communication a été modifiée. Les points ne sont pas reliés.")
    st.subheader("Versions documentaires observées")
    try:
        observations = read_observations(database(), case_history.iloc[-1].source_recall_id)
        table(observations)
        if not observations.empty and observations.groupby("role").size().max() == 1:
            st.caption("Une seule version observée par document : aucune évolution de la communication n'est démontrée.")
    except Exception:
        st.error("Les observations documentaires ne sont pas accessibles.")
    st.caption("observed_at est la date de capture. Un changement de source peut porter sur le texte, le fichier ou sa provenance ; comparez les empreintes. Aucune version antérieure n'est reconstruite.")
    detail(choose_analysis(case_history, "history_detail_id"))

elif page == "Traçabilité":
    st.caption("Les compteurs, exécutions ETL et dimensions ci-dessous couvrent l'entrepôt entier ; les filtres d'analyse ne les restreignent pas.")
    a, b, c = st.columns(3)
    a.metric("Analyses sources / faits", f"{counts['source_results']} / {counts['facts']}")
    b.metric("Versions documentaires", counts["document_versions"])
    c.metric("Exécutions d'analyse", counts["executions"])
    st.subheader("Chargements ETL récents")
    table(etl)
    st.caption("Un rechargement idempotent affiche inserted=0 et updated=0 lorsque les sources n'ont pas changé. Les exécutions d'analyse incluent aussi les échecs ; elles ne correspondent pas toutes à un fait.")
    try:
        companies, products = read_dimensions(database())
        with st.expander("Historisation SCD Type 2 · entreprises"):
            table(companies)
            st.caption("Sans organization_key vérifié, le nom est une clé de regroupement et ne garantit pas une identité juridique stable.")
        with st.expander("Historisation SCD Type 2 · produits"):
            table(products)
    except Exception:
        st.error("Les dimensions ne sont pas accessibles.")
    st.info("valid_from / valid_to délimitent les observations conservées. La date d'une analyse LLM n'est pas utilisée pour inventer l'état passé d'une entreprise ou d'un document.")

st.divider()
st.caption("ETICS · Analyse de la transparence des communications · Les verdicts sont des résultats automatiques à interpréter avec leurs preuves et leur protocole.")
