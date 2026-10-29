
PROMPT_VERSION = "questionnaire_v4_evidence"


from src.pipeline_io import PROJECT_ROOT
SYSTEM_PROMPT = (PROJECT_ROOT / "prompts" / f"{PROMPT_VERSION}.md").read_text(encoding="utf-8")

def build_user_prompt(rappelconso_text: str, communication_text: str) -> str:
    """Sépare explicitement la référence officielle du document analysé."""
    return f"""=== FICHE OFFICIELLE RAPPELCONSO ===
{rappelconso_text}

=== COMMUNICATION DE L'ENTREPRISE ===
{communication_text}
"""
