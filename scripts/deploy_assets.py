from __future__ import annotations

from scripts.create_search_index import main as deploy_index
from scripts.create_knowledgebase import main as deploy_knowledgebase


if __name__ == "__main__":
    deploy_index()
    deploy_knowledgebase()
