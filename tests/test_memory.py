"""Tests for memory subsystem: conversation window, project metadata, semantics."""
from memory import MemoryManager


def test_conversation_history(workspace):
    mm = MemoryManager(workspace)
    mm.add_chat_message("user", "hello")
    mm.add_chat_message("assistant", "hi there")
    history = mm.get_chat_history()
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "hello"


def test_context_includes_project_metadata(workspace):
    mm = MemoryManager(workspace)
    context = mm.build_context_for_query("buat fitur login")
    assert "PROJECT METADATA" in context
    assert "Workspace:" in context


def test_store_and_retrieve_knowledge(workspace):
    mm = MemoryManager(workspace)
    mm.store_knowledge("bug:nullref", "IndexError happens when list is empty")
    mm.store_knowledge("convention:snake", "Use snake_case for functions")
    context = mm.build_context_for_query("list kosong error")
    assert "RELEVANT PAST KNOWLEDGE" in context or "bug" in context


def test_semantic_store(workspace):
    mm = MemoryManager(workspace)
    mm.store_knowledge("doc1", "landing page needs hero section")
    assert mm.semantic.documents.get("doc1")
