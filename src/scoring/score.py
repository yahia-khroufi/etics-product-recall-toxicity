"""Point d'appel réservé au futur scoring ; aucune règle métier pour l'instant."""

import logging

LOGGER = logging.getLogger(__name__)


def score_case(case_id: str) -> None:
    """Signale explicitement que le score n'est pas encore calculé."""
    LOGGER.warning("%s : scoring non implémenté ; aucun score calculé.", case_id)
