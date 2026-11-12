from typing import List, Dict, Tuple

class SimpleVectorStore:
    """
    Implementasi pencarian semantik (TF-IDF sederhana) murni Python 
    tanpa dependensi eksternal (numpy/scipy) sebagai fallback yang aman.
    """
    def __init__(self):
        self.documents: Dict[str, str] = {}
        
    def add_document(self, doc_id: str, text: str) -> None:
        self.documents[doc_id] = text.lower()
        
    def _get_words(self, text: str) -> List[str]:
        return [w for w in text.split() if len(w) > 2]

    def search(self, query: str, top_k: int = 3) -> List[Tuple[str, float]]:
        query_words = self._get_words(query.lower())
        if not query_words or not self.documents:
            return []

        scores: List[Tuple[str, float]] = []
        for doc_id, text in self.documents.items():
            doc_words = self._get_words(text)
            score = 0.0
            for qw in query_words:
                tf = doc_words.count(qw) / (len(doc_words) + 1)
                score += tf
            if score > 0:
                scores.append((doc_id, score))
                
        # Urutkan berdasarkan skor tertinggi
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]
