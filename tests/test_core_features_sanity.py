import pytest
import os
from unittest.mock import MagicMock, patch
from app.services.rag_service import add_document, semantic_search
from app.services.report_service import ReportService

@pytest.mark.asyncio
async def test_rag_service_sanity():
    # Mock embedding model to avoid loading overhead
    with patch("app.services.rag_service._get_embedding_model") as mock_model_func:
        mock_model = MagicMock()
        mock_model.encode.return_value = [[0.1] * 384]
        mock_model_func.return_value = mock_model
        
        # Mock FAISS
        with patch("app.services.rag_service._get_faiss_index") as mock_faiss_func:
            mock_faiss = MagicMock()
            mock_faiss_func.return_value = mock_faiss
            
            # Mock DB
            with patch("app.core.database.SessionLocal") as mock_session_local:
                mock_db = MagicMock()
                mock_session_local.return_value = mock_db
                
                # 1. Test add_document
                result = await add_document("Title", "Content", "text")
                assert "document_id" in result
                assert result["status"] == "completed"
                
                # 2. Test semantic_search
                with patch("app.services.rag_service._load_metadata") as mock_load_meta:
                    mock_load_meta.return_value = {
                        "chunks": [{"document_id": "doc1", "chunk_id": "c1", "content": "Sample content"}]
                    }
                    mock_faiss.search.return_value = ([[0.9]], [[0]])
                    
                    search_results = await semantic_search("query")
                    assert len(search_results) > 0
                    assert search_results[0]["content"] == "Sample content"

@pytest.mark.asyncio
async def test_report_service_sanity():
    svc = ReportService()
    
    # Mock template rendering
    with patch.object(svc.template_env, "get_template") as mock_get_template:
        mock_template = MagicMock()
        mock_template.render.return_value = "<html>Report Content</html>"
        mock_get_template.return_value = mock_template
        
        # Mock file operations
        with patch("builtins.open", MagicMock()):
            with patch("os.makedirs", MagicMock()):
                messages = [
                    {"role": "user", "content": "Question"},
                    {"role": "assistant", "content": "Answer"}
                ]
                
                # Test HTML generation
                success = await svc.generate_report(
                    "session1", "Title", messages, "test.html", format="html"
                )
                assert success is True
                
                # Test Markdown generation
                success_md = await svc.generate_report(
                    "session1", "Title", messages, "test.md", format="md"
                )
                assert success_md is True
