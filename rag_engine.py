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

_BLOCK_TAGS = re.compile(
    r"(<\s*/?\s*(?:p|div|br|tr|h[1-6]|li|ul|ol|table|thead|tbody|section|article|blockquote)\b)",
    re.IGNORECASE,
)


def strip_html(text: str) -> str:
    """Remove HTML tags, preserving paragraph boundaries as double newlines."""
    # Insert paragraph breaks before block-level tags so boundaries survive
    text = _BLOCK_TAGS.sub(r"\n\n\1", text)
    soup = BeautifulSoup(text, "html.parser")
    clean = soup.get_text(separator=" ")
    # Collapse runs of whitespace within lines, but preserve double-newline breaks
    clean = re.sub(r"[ \t]+", " ", clean)
    clean = re.sub(r"\n[ \t]*\n[\n ]*", "\n\n", clean)
    return clean.strip()


def _get_overlap_paragraphs(paragraphs: list[str], overlap: int) -> list[str]:
    """Return trailing paragraphs from the list that fit within the overlap word budget."""
    result = []
    total = 0
    for para in reversed(paragraphs):
        words = len(para.split())
        if total + words > overlap and result:
            break
        result.append(para)
        total += words
    result.reverse()
    return result


def _get_overlap_sentences(sentences: list[str], overlap: int) -> list[str]:
    """Return trailing sentences that fit within the overlap word budget."""
    result = []
    total = 0
    for sent in reversed(sentences):
        words = len(sent.split())
        if total + words > overlap and result:
            break
        result.append(sent)
        total += words
    result.reverse()
    return result


def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 200) -> list[str]:
    """Split text into overlapping chunks using paragraph-aware splitting.

    Splits on double-newline paragraph boundaries first.  For oversized
    paragraphs, falls back to sentence-boundary splitting.
    """
    if not text or not text.strip():
        return []

    paragraphs = [p.strip() for p in re.split(r"\n\n+", text) if p.strip()]
    if not paragraphs:
        return []

    chunks: list[str] = []
    current_paras: list[str] = []
    current_words = 0

    def flush():
        nonlocal current_paras, current_words
        if current_paras:
            chunks.append("\n\n".join(current_paras))
            # Overlap: keep trailing paragraphs within budget
            overlap_paras = _get_overlap_paragraphs(current_paras, overlap)
            current_paras = list(overlap_paras)
            current_words = sum(len(p.split()) for p in current_paras)

    for para in paragraphs:
        para_words = len(para.split())

        if para_words > chunk_size:
            # Flush anything accumulated so far
            flush()

            # Split oversized paragraph by sentences
            sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", para) if s.strip()]
            if not sentences:
                sentences = [para]

            sent_buf: list[str] = list(current_paras)  # include overlap
            sent_words = current_words

            for sent in sentences:
                sw = len(sent.split())
                if sent_words + sw > chunk_size and sent_buf:
                    chunks.append(" ".join(sent_buf))
                    overlap_sents = _get_overlap_sentences(sent_buf, overlap)
                    sent_buf = list(overlap_sents)
                    sent_words = sum(len(s.split()) for s in sent_buf)
                sent_buf.append(sent)
                sent_words += sw

            if sent_buf:
                chunks.append(" ".join(sent_buf))
                overlap_sents = _get_overlap_sentences(sent_buf, overlap)
                current_paras = list(overlap_sents)
                current_words = sum(len(s.split()) for s in current_paras)
            else:
                current_paras = []
                current_words = 0
            continue

        if current_words + para_words > chunk_size and current_paras:
            flush()

        current_paras.append(para)
        current_words += para_words

    # Final flush without overlap
    if current_paras:
        chunks.append("\n\n".join(current_paras))

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
# Query helpers
# ---------------------------------------------------------------------------

def _build_where_filter(filing_types=None):
    """Build a ChromaDB ``where`` clause for filing type filtering.

    Date filtering is handled post-retrieval because ChromaDB's ``$gte``/``$lte``
    operators only work on numeric types, and ``filing_date`` is stored as a string.

    Args:
        filing_types: List of filing type strings to include (e.g. ["10-K", "10-Q"]).

    Returns:
        A dict suitable for ``collection.query(where=...)`` or ``None`` if no filter.
    """
    if filing_types:
        return {"filing_type": {"$in": filing_types}}
    return None


def _filter_by_date(documents, metadatas, distances, date_from=None, date_to=None):
    """Filter retrieved chunks by filing_date range (string comparison on ISO dates).

    Returns filtered (documents, metadatas, distances) tuples.
    """
    if not date_from and not date_to:
        return documents, metadatas, distances

    filtered_docs = []
    filtered_metas = []
    filtered_dists = []

    for doc, meta, dist in zip(documents, metadatas, distances):
        fd = meta.get("filing_date", "")
        if date_from and fd < date_from:
            continue
        if date_to and fd > date_to:
            continue
        filtered_docs.append(doc)
        filtered_metas.append(meta)
        filtered_dists.append(dist)

    return filtered_docs, filtered_metas, filtered_dists


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


def query(question: str, ticker: str, top_k: int = 15, model: str = None,
          date_from: str = None, date_to: str = None, filing_types: list[str] = None):
    """Query indexed filings and return a streamed Gemini response.

    Args:
        question: The user's question.
        ticker: Ticker symbol (ChromaDB collection name).
        top_k: Number of relevant chunks to retrieve.
        model: Gemini model name to use. Defaults to config.GEMINI_MODEL.
        date_from: Optional lower-bound date filter (inclusive).
        date_to: Optional upper-bound date filter (inclusive).
        filing_types: Optional list of filing types to include.

    Yields:
        First item may be a dict ``{"_sources": [...]}`` with source metadata.
        Subsequent items are str chunks of the Gemini response.
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

    # Build optional where filter (filing types only; dates filtered post-retrieval)
    where_filter = _build_where_filter(filing_types)

    # Retrieve relevant chunks (include distances for relevance threshold)
    query_kwargs = dict(
        query_embeddings=[query_embedding],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )
    if where_filter:
        query_kwargs["where"] = where_filter

    results = collection.query(**query_kwargs)

    documents = results["documents"][0] if results["documents"] else []
    metadatas = results["metadatas"][0] if results["metadatas"] else []
    distances = results["distances"][0] if results.get("distances") else []

    # Post-retrieval date filtering (ISO date strings compare lexicographically)
    if date_from or date_to:
        documents, metadatas, distances = _filter_by_date(
            documents, metadatas, distances, date_from, date_to,
        )

    if not documents:
        yield "No relevant content found in the indexed filings."
        return

    # Relevance threshold: drop chunks with cosine distance > 0.35
    # (cosine distance in ChromaDB = 1 - similarity, so 0.35 ≈ 0.65 similarity)
    # Always keep at least the single best chunk.
    DISTANCE_THRESHOLD = 0.35

    if distances:
        filtered = [
            (doc, meta, dist)
            for doc, meta, dist in zip(documents, metadatas, distances)
            if dist <= DISTANCE_THRESHOLD
        ]
        # Always keep at least the best chunk
        if not filtered:
            best_idx = distances.index(min(distances))
            filtered = [(documents[best_idx], metadatas[best_idx], distances[best_idx])]
        documents = [f[0] for f in filtered]
        metadatas = [f[1] for f in filtered]

    # Emit source metadata as first item
    seen = set()
    sources = []
    for meta in metadatas:
        key = (meta.get("filing_type", ""), meta.get("filing_date", ""))
        if key not in seen and key != ("", ""):
            seen.add(key)
            sources.append({"filing_type": key[0], "filing_date": key[1]})
    if sources:
        yield {"_sources": sources}

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

    # Stream response from Gemini (with thinking support for Gemini 2.5+)
    try:
        gen_model = genai.GenerativeModel(
            model,
            generation_config={"thinking_config": {"includeThoughts": True}},
        )
    except Exception:
        gen_model = genai.GenerativeModel(model)

    response = gen_model.generate_content(prompt, stream=True)
    for chunk in response:
        try:
            parts = chunk.candidates[0].content.parts
            for part in parts:
                if hasattr(part, "thought") and part.thought:
                    if part.text:
                        yield {"_thinking": part.text}
                elif part.text:
                    yield part.text
        except (AttributeError, IndexError):
            # Fallback for models that don't support thinking / parts access
            if chunk.text:
                yield chunk.text
