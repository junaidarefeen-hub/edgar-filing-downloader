"""RAG engine — index SEC filings into ChromaDB and query with Gemini."""

import hashlib
import os
import re

import chromadb
import google.generativeai as genai
from bs4 import BeautifulSoup

import config


# ---------------------------------------------------------------------------
# Text processing
# ---------------------------------------------------------------------------

def strip_html(text: str) -> str:
    """Remove HTML tags and collapse whitespace."""
    soup = BeautifulSoup(text, "html.parser")
    clean = soup.get_text(separator=" ")
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 200) -> list[str]:
    """Split text into overlapping chunks by approximate token count (words)."""
    words = text.split()
    if not words:
        return []
    chunks = []
    start = 0
    while start < len(words):
        end = start + chunk_size
        chunk = " ".join(words[start:end])
        chunks.append(chunk)
        if end >= len(words):
            break
        start = end - overlap
    return chunks


# ---------------------------------------------------------------------------
# ChromaDB client
# ---------------------------------------------------------------------------

_chroma_client = None


def _get_chroma_client():
    """Get or create the persistent ChromaDB client."""
    global _chroma_client
    if _chroma_client is None:
        os.makedirs(config.VECTOR_STORE_DIR, exist_ok=True)
        _chroma_client = chromadb.PersistentClient(path=config.VECTOR_STORE_DIR)
    return _chroma_client


def reset_chroma_client():
    """Reset the ChromaDB client (useful for testing)."""
    global _chroma_client
    _chroma_client = None


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------

def _file_hash(path: str) -> str:
    """Return a short hash of the file path for use as document ID prefix."""
    return hashlib.md5(path.encode()).hexdigest()[:12]


def index_filings(file_paths: list[str], ticker: str, progress_callback=None) -> dict:
    """Index filing files into ChromaDB.

    Args:
        file_paths: List of absolute file paths to filing documents.
        ticker: Ticker symbol, used as ChromaDB collection name.
        progress_callback: Optional callable(current, total, message) for progress updates.

    Returns:
        dict with keys: indexed (int), skipped (int), total_chunks (int)
    """
    genai.configure(api_key=config.GEMINI_API_KEY)
    client = _get_chroma_client()
    collection = client.get_or_create_collection(
        name=ticker.upper(),
        metadata={"hnsw:space": "cosine"},
    )

    stats = {"indexed": 0, "skipped": 0, "total_chunks": 0}

    for i, path in enumerate(file_paths):
        file_id = _file_hash(path)

        # Check if already indexed
        existing = collection.get(where={"source_file": path}, limit=1)
        if existing and existing["ids"]:
            stats["skipped"] += 1
            if progress_callback:
                progress_callback(i + 1, len(file_paths), f"Skipped (already indexed): {os.path.basename(path)}")
            continue

        # Read and process file
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            raw = f.read()

        text = strip_html(raw)
        if not text.strip():
            stats["skipped"] += 1
            if progress_callback:
                progress_callback(i + 1, len(file_paths), f"Skipped (empty): {os.path.basename(path)}")
            continue

        chunks = chunk_text(text)
        if not chunks:
            stats["skipped"] += 1
            continue

        # Extract metadata from path: .../TICKER/FORM_TYPE/DATE_ACCESSION/file
        parts = path.replace("\\", "/").split("/")
        filing_type = ""
        filing_date = ""
        for j, part in enumerate(parts):
            if part.upper() == ticker.upper() and j + 2 < len(parts):
                filing_type = parts[j + 1]
                date_acc = parts[j + 2]
                filing_date = date_acc.split("_")[0] if "_" in date_acc else ""
                break

        # Embed chunks
        if progress_callback:
            progress_callback(i + 1, len(file_paths), f"Embedding: {os.path.basename(path)} ({len(chunks)} chunks)")

        # Batch embed (Gemini supports batch embedding)
        result = genai.embed_content(
            model=f"models/{config.GEMINI_EMBEDDING_MODEL}",
            content=chunks,
            task_type="retrieval_document",
        )
        embeddings = result["embedding"]

        # Upsert into ChromaDB
        ids = [f"{file_id}_chunk_{ci}" for ci in range(len(chunks))]
        metadatas = [
            {
                "source_file": path,
                "filing_type": filing_type,
                "filing_date": filing_date,
                "chunk_index": ci,
            }
            for ci in range(len(chunks))
        ]

        # ChromaDB has a batch size limit; upsert in batches of 100
        batch_size = 100
        for b in range(0, len(ids), batch_size):
            collection.upsert(
                ids=ids[b:b + batch_size],
                embeddings=embeddings[b:b + batch_size],
                documents=chunks[b:b + batch_size],
                metadatas=metadatas[b:b + batch_size],
            )

        stats["indexed"] += 1
        stats["total_chunks"] += len(chunks)

        if progress_callback:
            progress_callback(i + 1, len(file_paths), f"Indexed: {os.path.basename(path)}")

    return stats


# ---------------------------------------------------------------------------
# Querying
# ---------------------------------------------------------------------------

def get_indexed_files(ticker: str) -> set[str]:
    """Return set of file paths that have been indexed for a ticker."""
    try:
        client = _get_chroma_client()
        collection = client.get_collection(name=ticker.upper())
        results = collection.get(include=["metadatas"])
        return {m["source_file"] for m in results["metadatas"] if "source_file" in m}
    except Exception:
        return set()


def query(question: str, ticker: str, top_k: int = 10, model: str = None):
    """Query indexed filings and return a streamed Gemini response.

    Args:
        question: The user's question.
        ticker: Ticker symbol (ChromaDB collection name).
        top_k: Number of relevant chunks to retrieve.
        model: Gemini model name to use. Defaults to config.GEMINI_MODEL.

    Yields:
        str chunks of the Gemini response.
    """
    model = model or config.GEMINI_MODEL
    genai.configure(api_key=config.GEMINI_API_KEY)
    client = _get_chroma_client()

    try:
        collection = client.get_collection(name=ticker.upper())
    except Exception:
        yield "No indexed filings found for this ticker. Please index some filings first."
        return

    # Embed the question
    q_result = genai.embed_content(
        model=f"models/{config.GEMINI_EMBEDDING_MODEL}",
        content=question,
        task_type="retrieval_query",
    )
    query_embedding = q_result["embedding"]

    # Retrieve relevant chunks
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
        include=["documents", "metadatas"],
    )

    documents = results["documents"][0] if results["documents"] else []
    metadatas = results["metadatas"][0] if results["metadatas"] else []

    if not documents:
        yield "No relevant content found in the indexed filings."
        return

    # Build context with source info
    context_parts = []
    for doc, meta in zip(documents, metadatas):
        source_info = f"[{meta.get('filing_type', 'Unknown')} {meta.get('filing_date', '')}]"
        context_parts.append(f"{source_info}\n{doc}")
    context = "\n\n---\n\n".join(context_parts)

    # Build prompt
    prompt = f"""You are an expert financial analyst. Answer the user's question based on the following SEC filing excerpts.
Cite the filing type and date when referencing specific information.
If the context doesn't contain enough information to answer, say so.

CONTEXT FROM SEC FILINGS:
{context}

USER QUESTION: {question}

ANSWER:"""

    # Stream response from Gemini
    gen_model = genai.GenerativeModel(model)
    response = gen_model.generate_content(prompt, stream=True)
    for chunk in response:
        if chunk.text:
            yield chunk.text
