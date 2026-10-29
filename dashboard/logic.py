import pandas as pd

from src.llm.analyze import _normalized, verify_evidence
from src.llm.schemas import QuestionnaireAnswers

STATUSES = {"scored": "Scorée", "needs_review": "À revoir",
            "legacy_unlinked": "Historique sans source liée", "pending": "En attente"}
QUESTIONS = {
    "q1": "Danger explicitement nommé",
    "q2": "Cause ou origine indiquée",
    "q3": "Conduite à tenir indiquée",
    "q4": "Produit précisément identifiable",
    "q5": "Solution ou droit indiqué",
    "q6": "Formulation vague ou ambiguë",
    "q7": "Minimisation face à la fiche officielle",
}


def latest_per_case(frame):
    return frame.sort_values(["analysis_created_at", "source_result_id"]).drop_duplicates("case_id", keep="last")


def scored_cohort(frame):
    return frame.loc[(frame.analysis_status == "scored") & frame.evidence_verified.eq(True)
                     & frame.toxicity_score.notna()]


def filter_history(frame, *, search="", choices=None, date_range=None):
    selected = frame.copy()
    for column, values in (choices or {}).items():
        if values:
            selected = selected[selected[column].fillna("Non renseigné").isin(values)]
    if search.strip():
        mask = pd.Series(False, index=selected.index)
        for column in ("case_id", "company_name", "product_name", "brand"):
            mask |= selected[column].fillna("").str.contains(search.strip(), case=False, regex=False)
        selected = selected[mask]
    if date_range:
        dates = selected.analysis_created_at.dt.tz_convert("Africa/Casablanca").dt.date
        selected = selected[(dates >= date_range[0]) & (dates <= date_range[1])]
    return selected


def presentation_table(frame):
    result = frame[["source_result_id", "case_id", "company_name", "product_name", "brand",
                    "analysis_status", "toxicity_score", "completeness_score", "verdict",
                    "model_name", "prompt_version", "scoring_version", "analysis_created_at"]].copy()
    result["analysis_status"] = result.analysis_status.map(STATUSES).fillna(result.analysis_status)
    result["completeness_score"] = result.completeness_score * 100
    result["analysis_created_at"] = result.analysis_created_at.dt.tz_convert("Africa/Casablanca").dt.strftime("%d/%m/%Y %H:%M")
    result.columns = ["Analyse", "Rappel", "Entreprise", "Produit", "Marque", "Statut",
                      "Indice / 100", "Complétude %", "Verdict enregistré", "Modèle",
                      "Prompt", "Règles de scoring", "Date (Casablanca)"]
    return result.sort_values(["Rappel", "Analyse"], ascending=[True, False])


def csv_bytes(frame):
    # Protect spreadsheet users from formula interpretation in imported document text.
    safe = frame.copy()
    for column in safe:
        safe[column] = safe[column].map(
            lambda value: "'" + value if isinstance(value, str)
            and value.lstrip().startswith(("=", "+", "-", "@")) else value)
    return safe.to_csv(index=False, sep=";").encode("utf-8-sig")


def quote_diagnostics(row, documents):
    """Explain lexical validation with the same normalizer and validator as ingestion."""
    official = documents.get("official", {}).get("raw_text", "")
    company = documents.get("company", {}).get("raw_text", "")
    if not official or not company:
        return None, [], ["Les deux documents utilisés par cette analyse ne sont pas disponibles."]
    evidence = row["evidence"] or {}
    answers = QuestionnaireAnswers(**{key: {"value": bool(row[key]), "evidence": evidence.get(key, [])}
                                      for key in QUESTIONS})
    reasons, quotes = [], []
    for key in QUESTIONS:
        answer = getattr(answers, key)
        if answer.value and not answer.evidence:
            reasons.append(f"{key.upper()} : réponse Oui sans citation.")
        normalized = [_normalized(q) for q in answer.evidence]
        for quote, norm in zip(answer.evidence, normalized):
            in_official = bool(norm) and norm in _normalized(official)
            in_company = bool(norm) and norm in _normalized(company)
            source = "Les deux" if in_official and in_company else "Officiel" if in_official else "Entreprise" if in_company else "Introuvable"
            valid = bool(norm) and norm in _normalized(official + "\n" + company) if key == "q7" else in_company
            quotes.append({"Question": key.upper(), "Citation": quote, "Retrouvée dans": source,
                           "Source attendue": "Comparaison des deux documents" if key == "q7" else "Entreprise",
                           "Présence conforme": bool(valid)})
            if not valid:
                reasons.append(f"{key.upper()} : citation absente de la source attendue.")
        if key == "q7" and answer.value:
            off, com = _normalized(official), _normalized(company)
            if not (any(q and q in off and q not in com for q in normalized)
                    and any(q and q in com and q not in off for q in normalized)):
                reasons.append("Q7 : la comparaison n'a pas une citation propre à chaque document.")
    return verify_evidence(answers, official, company), quotes, list(dict.fromkeys(reasons))
