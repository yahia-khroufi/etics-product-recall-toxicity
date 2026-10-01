
PROMPT_VERSION = "questionnaire_v3"

SYSTEM_PROMPT = """Tu analyses la transparence d'une communication de rappel de produit.
Réponds uniquement à partir des deux documents fournis. N'invente aucune information.
Pour chaque réponse positive, fournis au moins une citation exacte et courte.
Pour une information absente, réponds false avec une liste evidence vide.
Q1 à Q6 portent uniquement sur la communication de l'entreprise.
Q7 compare la communication à la fiche officielle RappelConso.

Q1 : le danger est-il nommé explicitement ?
Q2 : la cause du défaut est-elle indiquée ?
Q3 : la conduite à tenir par le consommateur est-elle indiquée ?
Q4 : le produit est-il identifiable (référence, lot, GTIN ou dates) ?
Q5 : les droits du consommateur sont-ils indiqués (remboursement, échange, réparation) ?
Q6 : la communication contient-elle une formulation vague ou euphémisante sans préciser le danger ?
Q7 : la gravité est-elle atténuée par rapport au motif ou au risque de la fiche officielle ?
"""


def build_user_prompt(rappelconso_text: str, communication_text: str) -> str:
    """Sépare explicitement la référence officielle du document analysé."""
    return f"""=== FICHE OFFICIELLE RAPPELCONSO ===
{rappelconso_text}

=== COMMUNICATION DE L'ENTREPRISE ===
{communication_text}
"""
