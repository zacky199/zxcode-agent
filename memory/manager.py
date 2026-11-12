from pathlib import Path
from typing import List, Dict, Any

from .conversation import ConversationMemory
from .project import ProjectMemory
from .semantic import SemanticMemory
from .retrieval import ContextRetriever

class MemoryManager:
    """Facade Pattern: Mengoordinasikan seluruh sub-sistem memori agen dalam satu pintu."""
    
    def __init__(self, workspace: Path, model_name: str = "gpt-4o"):
        self.conversation = ConversationMemory(model=model_name)
        self.project = ProjectMemory(workspace)
        self.semantic = SemanticMemory()
        self.retriever = ContextRetriever(self.semantic)
        
        # Pindai karakteristik proyek secara otomatis saat diinisialisasi
        self.project.scan_project_meta()

    def add_chat_message(self, role: str, content: str) -> None:
        """Menyimpan pesan ke riwayat obrolan jangka pendek (Sliding Window)."""
        self.conversation.add_message(role, content)
        self.conversation.trim_history()

    def get_chat_history(self) -> List[Dict[str, Any]]:
        """Mendapatkan array pesan yang aman dan tidak melampaui batas token LLM."""
        return self.conversation.history

    def store_knowledge(self, doc_id: str, content: str) -> None:
        """Menyimpan snippet kode atau fakta penting ke memori jangka panjang (Vector Store)."""
        self.semantic.add_document(doc_id, content)

    def build_context_for_query(self, user_query: str) -> str:
        """
        Merakit konteks super yang menggabungkan metadata proyek dan 
        ingatan masa lalu yang relevan (Retrieval) dengan prompt pengguna saat ini.
        """
        # 1. Ekstrak Metadata Proyek
        meta = self.project.metadata
        project_context = (
            f"[PROJECT METADATA]\n"
            f"- Workspace: {meta.get('path')}\n"
            f"- Git Initialized: {meta.get('has_git')}\n"
            f"- Package Manager: {'Yes' if meta.get('has_package_json') else 'No'}\n"
        )
        # Tooling hints help the agent choose the right build/test commands.
        if meta.get("dominant_language") not in (None, "Unknown"):
            project_context += f"- Language: {meta['dominant_language']}\n"
        if meta.get("frameworks"):
            project_context += f"- Frameworks: {', '.join(meta['frameworks'])}\n"
        if meta.get("js_package_manager"):
            project_context += f"- JS package manager: {meta['js_package_manager']}\n"
        if meta.get("verify_commands"):
            project_context += (f"- Suggested verify commands: "
                                f"{', '.join(meta['verify_commands'])}\n")
        
        # 2. Tarik Memori Semantik (RAG Dasar)
        relevant_docs = self.retriever.retrieve_relevant_context(user_query, top_k=2)
        semantic_context = ""
        if relevant_docs:
            semantic_context = "[RELEVANT PAST KNOWLEDGE]\n"
            for doc in relevant_docs:
                # Mengambil teks asli dari dictionary vector store
                content = self.semantic.documents.get(doc, "")
                semantic_context += f"- {doc}: {content[:200]}...\n"
                
        return f"{project_context}\n{semantic_context}".strip()
