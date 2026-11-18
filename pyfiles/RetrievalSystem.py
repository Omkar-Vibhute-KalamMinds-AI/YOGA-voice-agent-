import sys
from typing import Any, Dict, List

import pyfiles_path  # noqa: F401
from exception import RetrievialSystemException
from HybridSearch import Hybrid_search
from logger import logger
from tracing import traceable

try:
    import lancedb  # noqa: F401
except ModuleNotFoundError as exc:
    raise ModuleNotFoundError(
        "lancedb is not installed. From the project folder run: uv pip install lancedb"
    ) from exc


class RAGRetriever:
    """Handles query-based retrieval from the vector store"""

    def __init__(self, lancedb_table=None, embedding_manager=None, vectore_store=None, **kwargs):
        """
        Initialize the retriever

        Args:
            lancedb_table / vectore_store: LanceDB table with document embeddings.
            embedding_manager (EmbeddingManager): for generating the query embeddings.
        """
        table = lancedb_table if lancedb_table is not None else (vectore_store or kwargs.get("vector_store"))
        if table is None or embedding_manager is None:
            raise ValueError("RAGRetriever needs a LanceDB table and an embedding_manager")
        self.lancedb_table = table
        self.embedding_manager = embedding_manager

    @traceable(name="retrieve", run_type="retriever")
    def retrieve(self, query: str, top_k: int = 1) -> List[Dict[str, Any]]:
        """
        Retrieves relevant documents for the query

        Args:
            query (str): user query
            top_k (int): Number of top results for the query. Defaults to 1.
        
        Returns:
            List[Dict[str, Any]]: list of dicts containing retrieved documents and metadata.
        """
    
        try:
            docs = Hybrid_search(
                query,
                self.lancedb_table,
                self.embedding_manager,
                top_k=top_k,
            )

            retrieved_docs = []
            for i, doc in enumerate(docs):
                retrieved_docs.append({
                    "id": doc.metadata.get("id"),
                    "chunk_index": doc.metadata.get("chunk_index"),
                    "content": doc.page_content,
                    "source_file": doc.metadata.get("source_file"),
                    "rank": i + 1,
                })
            return retrieved_docs

        except Exception as e:
            logger.error(f"Retrieval failed for query '{query} of length {len(query)}': {e}", exc_info=True)
            raise RetrievialSystemException(
                f"Retrieval failed for query '{query}': {e}",
                sys,
            ) from e



