"""New-case form; analysis is executed by the independent durable worker."""
from datetime import datetime, timezone

import streamlit as st

from dashboard.data import read_connection, read_jobs
from src.intake import submit_case, IntakeError

JOB_LABELS = {"queued": "En file", "preparing": "Extraction et préparation",
              "analyzing": "Analyse LLM", "dwh_pending": "Scoring / chargement DWH en attente",
              "completed": "Terminé", "needs_review": "Terminé · citations à revoir",
              "retry_wait": "API indisponible · reprise prévue", "failed": "Erreur · intervention requise",
              "interrupted": "Analyse interrompue · reprise à autoriser", "skipped_existing": "Déjà présent"}


@st.fragment(run_every="10s")
def treatment_status(engine):
    st.subheader("Suivi des nouveaux rappels")
    try:
        jobs, worker = read_jobs(engine)
    except Exception:
        st.warning("Le suivi des traitements est indisponible. Vérifiez PostgreSQL et la migration 004_pipeline_jobs.sql.")
        return
    if worker and worker["status"] != "stopped":
        age = (datetime.now(timezone.utc) - worker["heartbeat_at"]).total_seconds()
        if age < 900:
            labels = {"idle": "En attente de nouveaux dépôts", "processing": "Traitement en cours", "waiting_api": "En attente de disponibilité API"}
            st.caption(f"Processus ETICS : {labels.get(worker['status'], worker['status'])} · dernière activité il y a {max(0, int(age))} s.")
            if worker.get("next_attempt_at"):
                st.caption(f"Prochaine tentative API : {worker['next_attempt_at'].isoformat()}")
        else:
            st.warning("Le processus ETICS n'a pas signalé d'activité récente. Lancez Lancer_traitement_auto.ps1.")
    else:
        st.warning("Le processus automatique est arrêté. Lancez Lancer_traitement_auto.ps1 pour traiter les dépôts.")
    receipt = st.session_state.get("submitted_case")
    if receipt and (jobs.empty or receipt not in set(jobs.case_id)):
        st.info(f"Rappel {receipt} reçu. Le processus doit détecter son dépôt complet à la prochaine passe.")
    if jobs.empty:
        st.caption("Aucun traitement issu du formulaire pour le moment. Le corpus existant n'est pas relancé.")
    else:
        shown = jobs.copy()
        shown["status"] = shown.status.map(JOB_LABELS).fillna(shown.status)
        st.dataframe(shown.rename(columns={"case_id": "Rappel", "status": "État", "attempts": "Tentatives LLM",
                                          "result_id": "Analyse", "error_type": "Type d'erreur",
                                          "next_attempt_at": "Prochaine reprise", "created_at": "Déposé en file",
                                          "updated_at": "Dernière étape", "finished_at": "Terminé le"}),
                     hide_index=True, width="stretch")
    st.caption("Suivi actualisé toutes les 10 secondes pendant que cette page est ouverte. Les erreurs n'effacent pas les documents ou les réponses déjà enregistrées.")


def render_intake(engine):
    st.title("Nouveau rappel")
    st.write("Déposez la fiche officielle et la communication de l'entreprise. Le traitement automatique produira les réponses, vérifiera les citations, puis alimentera le Data Warehouse.")
    st.caption("PDF ou HTML UTF-8 · 20 Mo maximum par document · Les identifiants existants et les paires de fichiers identiques sont refusés.")
    with st.form("new_recall", clear_on_submit=False):
        case_id = st.text_input("Identifiant du nouveau rappel (facultatif)", placeholder="Automatique, par exemple 066", key="new_case_id")
        left, right = st.columns(2)
        with left:
            official = st.file_uploader("Fiche officielle RappelConso", type=["pdf", "html", "htm"], key="official_upload")
            official_url = st.text_input("Lien de la fiche officielle (facultatif)", key="official_url")
        with right:
            company = st.file_uploader("Communication de l'entreprise", type=["pdf", "html", "htm"], key="company_upload")
            company_url = st.text_input("Lien de la communication (facultatif)", key="company_url")
        st.caption("L'envoi déclenche des appels aux API LLM configurées dans le projet ; leur tarification et leurs quotas s'appliquent.")
        submitted = st.form_submit_button("Envoyer et analyser", type="primary", width="stretch")
    if submitted:
        if official is None or company is None:
            st.error("Ajoutez les deux documents avant d'envoyer le rappel.")
        else:
            try:
                with read_connection(engine) as connection:
                    received = submit_case(connection, case_id, (official.name, official.getvalue()),
                                           (company.name, company.getvalue()), official_url=official_url,
                                           company_url=company_url)
                st.session_state["submitted_case"] = received
                st.success(f"Rappel {received} enregistré. Son analyse sera prise en charge automatiquement par le processus ETICS.")
            except IntakeError as error:
                st.error(str(error))
            except Exception:
                st.error("Le dépôt n'a pas pu être confirmé. Consultez le suivi avant de renvoyer les fichiers.")
    treatment_status(engine)
