import hashlib
import logging

import google.generativeai as genai
from flask import current_app

logger = logging.getLogger(__name__)

def _embedding(text, task_type):
    response = genai.embed_content(
        model=current_app.config["RAG_EMBEDDING_MODEL"],
        content=text,
        task_type=task_type,
    )
    return response["embedding"]

def _chunk_text(text, max_size=500, overlap=50):
    lines = [line.strip() for line in text.splitlines()]
    
    paragraphs = []
    current_para = []
    for line in lines:
        if line:
            current_para.append(line)
        else:
            if current_para:
                paragraphs.append(" ".join(current_para))
                current_para = []
    if current_para:
        paragraphs.append(" ".join(current_para))
        
    chunks = []
    current_chunk = ""
    
    for para in paragraphs:
        if not current_chunk:
            current_chunk = para
        elif len(current_chunk) + 1 + len(para) <= max_size:
            current_chunk += " " + para
        else:
            chunks.append(current_chunk)
            if overlap > 0 and len(current_chunk) > overlap:
                overlap_text = current_chunk[-overlap:]
                space_idx = overlap_text.find(' ')
                if space_idx != -1:
                    overlap_text = overlap_text[space_idx+1:]
                current_chunk = overlap_text + " " + para
            else:
                current_chunk = para
                
    if current_chunk:
        chunks.append(current_chunk)
        
    return chunks

def clear_user_rag_documents(owner_id, supabase):
    """Delete all RAG documents for a user."""
    try:
        supabase.table("rag_documents").delete().contains("metadata", {"owner_id": str(owner_id)}).execute()
    except Exception as e:
        logger.error("Error clearing user RAG documents for owner_id %s: %s", owner_id, e)

def retrieve_context(owner_id, question, fallback_context, supabase):
    """Index the current private portal context and retrieve relevant chunks."""
    try:
        genai.configure(api_key=current_app.config["GOOGLE_API_KEY"])
        chunks = _chunk_text(fallback_context, max_size=500, overlap=50)
        rows = []
        for chunk in chunks:
            if not chunk.strip():
                continue
            source_key = hashlib.sha256(f"{owner_id}:{chunk}".encode()).hexdigest()
            rows.append({
                "source_key": source_key,
                "content": chunk,
                "metadata": {"owner_id": str(owner_id)},
                "embedding": _embedding(chunk, "retrieval_document"),
            })
        if rows:
            supabase.table("rag_documents").upsert(rows, on_conflict="source_key").execute()
        matches = supabase.rpc("match_rag_documents", {
            "query_embedding": _embedding(question, "retrieval_query"),
            "match_threshold": 0.2,
            "match_count": 6,
            "owner_id": str(owner_id),
        }).execute().data
        return "\n".join(item["content"] for item in matches) or fallback_context
    except Exception as e:
        logger.error("Error in retrieve_context for owner_id %s: %s", owner_id, e)
        return fallback_context
