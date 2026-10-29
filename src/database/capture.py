"""Archive current extracted texts and source provenance without calling an LLM."""
import argparse

from sqlalchemy import select

from src.database.db import SessionLocal
from src.database.models import Recall
from src.database.provenance import capture_recall, capture_document, source_metadata
from src.pipeline_io import EXTRACTED_DIR, case_folders
from src.preprocessing.clean import clean_text
from src.preprocessing.preprocess import read_text


def capture_case(case_id, factory=SessionLocal):
    _, folder = case_folders(EXTRACTED_DIR,[case_id])[0]
    with factory() as session:
        recall = session.scalar(select(Recall).where(Recall.case_id == case_id).with_for_update())
        if recall is None:
            raise LookupError(f"Load recall {case_id} before capturing sources")
        capture_recall(session,recall)
        ids = {}
        for role, filename in (("official","rappelconso.txt"),("company","communication_entreprise.txt")):
            content = clean_text(read_text(folder / filename))
            if content:
                ids[role] = capture_document(session,recall.id,role,content,source_metadata(case_id,role)).id
        session.commit()
        return ids


def main():
    parser=argparse.ArgumentParser(description="Capture observed sources; no historical reconstruction or LLM call")
    parser.add_argument("--case",action="append")
    args=parser.parse_args()
    for case_id,_ in case_folders(EXTRACTED_DIR,args.case):
        print(case_id,capture_case(case_id))


if __name__ == "__main__":
    main()
