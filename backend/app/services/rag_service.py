from typing import Awaitable, Callable, List

import httpx

from app.config import settings
from app.services.citation_service import extract_citations


RAG_SYSTEM_PROMPT = """You are a knowledgeable research assistant with access to multiple documents. Follow these rules strictly:

1. Answer the question only from relevant evidence in the provided materials.
2. If multiple documents provide relevant evidence, synthesize them. Do not cite a document merely to increase coverage.
3. Mark every factual claim with a citation number in brackets: [1], [2], etc.
4. Structure longer answers with headings (##), bullet points, or numbered lists for readability.
5. Include relevant details, examples, and context from the materials.
6. NEVER fabricate information, sources, or references.
7. If evidence is insufficient, say so explicitly. Never force a citation to an irrelevant excerpt.

Materials:
{materials}

Question: {query}

Provide a detailed, well-structured answer that incorporates information from all available documents. Cite sources as [N]:"""


class RAGService:

    def __init__(self):
        self.api_key = settings.deepseek_api_key
        self.base_url = settings.deepseek_base_url
        self.model = settings.deepseek_model

    def build_materials(self, chunks: List[dict]) -> str:
        parts = []
        for i, chunk in enumerate(chunks):
            src = chunk.get("section_title") or chunk.get("document_title", "unknown")
            parts.append(
                f"[{i + 1}] Document: {chunk.get('document_title', 'unknown')}"
                f" | section: {src}\n{chunk['content']}"
            )
        return "\n\n".join(parts)

    def build_prompt(self, query: str, chunks: List[dict], history: List[dict] = None) -> str:
        materials = self.build_materials(chunks)
        return RAG_SYSTEM_PROMPT.format(materials=materials, query=query)

    def build_messages(
        self, query: str, chunks: List[dict], history: List[dict] = None,
        allow_general_knowledge: bool = False,
    ) -> List[dict]:
        doc_names = sorted(set(c.get("document_title", "unknown") for c in chunks))
        doc_list = "\n".join(f"- {n}" for n in doc_names)

        if allow_general_knowledge:
            sys_msg = f"""You are a knowledgeable research assistant. You have materials from {len(doc_names)} documents:
{doc_list or '- No documents selected'}

Use the supplied materials when they are relevant, but do not limit the answer to them. For conceptual, explanatory, or extension questions, supplement with your reliable general knowledge. Cite supplied materials as [N]. Never invent citations. Clearly introduce substantial information not found in the materials with '扩展说明：'. If the materials conflict with general knowledge, point out the difference. Be detailed and well structured."""
        else:
            sys_msg = f"You are a research assistant. Answer only using materials from {len(doc_names)} documents:\n{doc_list}\n\nReference relevant documents in your answer. Cite as [N]. Never add unsupported facts."

        messages = [{"role": "system", "content": sys_msg}]

        if history:
            for h in history[-6:]:
                messages.append({"role": h["role"], "content": h["content"]})

        materials = self.build_materials(chunks)
        if materials:
            user_msg = f"Here are document excerpts:\n{materials}\n\nQuestion: {query}\n\nUse citations for claims drawn from these excerpts."
        elif not allow_general_knowledge:
            user_msg = f"Question: {query}\n\nNo supporting document excerpts are available. State clearly that the materials do not contain enough evidence. Do not answer from general knowledge and do not create citations."
        else:
            user_msg = f"Question: {query}\n\nNo document excerpts are available. Answer from reliable general knowledge and do not create citations."
        messages.append({"role": "user", "content": user_msg})
        return messages

    async def generate(
        self, query: str, chunks: List[dict], history: List[dict] = None,
        allow_general_knowledge: bool = False,
        token_callback: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict:
        messages = self.build_messages(query, chunks, history, allow_general_knowledge)

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 4000,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        usage = {}
        if token_callback:
            import json
            answer_parts: list[str] = []
            payload.update({"stream": True, "stream_options": {"include_usage": True}})
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream(
                    "POST", f"{self.base_url}/chat/completions", headers=headers, json=payload,
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if not raw or raw == "[DONE]":
                            continue
                        data = json.loads(raw)
                        usage = data.get("usage") or usage
                        delta = ((data.get("choices") or [{}])[0].get("delta") or {}).get("content") or ""
                        if delta:
                            answer_parts.append(delta)
                            await token_callback(delta)
            answer = "".join(answer_parts)
        else:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions", headers=headers, json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
            answer = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})

        citations = self._extract_citations(answer, chunks)

        return {
            "answer": answer,
            "citations": citations,
            "token_usage": {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
        }

    def _extract_citations(self, answer: str, chunks: List[dict]) -> List[dict]:
        return extract_citations(answer, chunks)

    async def rerank(self, query: str, chunks: List[dict], top_k: int = 5) -> List[dict]:
        """Use LLM to rerank chunks by relevance to query."""
        if len(chunks) <= top_k:
            return chunks

        prompt = f"""Rate each text chunk's relevance to the query on a scale of 0-10.
Query: {query}

"""
        for i, c in enumerate(chunks):
            prompt += f"[{i}] {c['content'][:300]}\n\n"
        prompt += """Return only JSON: {"scores": [{"index": 0, "score": 8.5}, ...]}"""

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                    "max_tokens": 500,
                    "response_format": {"type": "json_object"},
                },
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]

        import json
        try:
            scored = json.loads(content)
            for item in scored.get("scores", []):
                idx = item["index"]
                if idx < len(chunks):
                    chunks[idx]["rerank_score"] = item["score"]
            chunks.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)
        except (json.JSONDecodeError, KeyError):
            pass

        return chunks[:top_k]
