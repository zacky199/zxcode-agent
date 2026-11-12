from typing import List
from .semantic import SemanticMemory

class ContextRetriever:
    """Mengambil ingatan paling relevan dari SemanticMemory berdasarkan query."""
    
    def __init__(self, semantic_memory: SemanticMemory):
        self.semantic_memory = semantic_memory

    def retrieve_relevant_context(self, query: str, top_k: int = 3) -> List[str]:
        """
        Mengembalikan daftar document_id (misal: path file atau judul memori) 
        yang memiliki skor semantik tertinggi terhadap query.
        """
        results = self.semantic_memory.search(query, top_k=top_k)
        # result berbentuk tuple (doc_id, score)
        return [doc_id for doc_id, score in results]
