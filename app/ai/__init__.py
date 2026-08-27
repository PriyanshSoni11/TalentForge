from flask import current_app
from langchain_google_genai import ChatGoogleGenerativeAI

_llm = None


def extract_llm_text(content):
    """Extract clean string text from any LangChain/Gemini response content format."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and "text" in item:
                parts.append(item["text"])
            elif hasattr(item, "text"):
                parts.append(getattr(item, "text", ""))
        return "\n".join(parts)
    if hasattr(content, "text"):
        return getattr(content, "text", str(content))
    return str(content or "")


def get_llm(timeout=60):
    global _llm
    model_name = current_app.config.get("GEMINI_MODEL", "gemini-3.6-flash")
    api_key = current_app.config.get("GOOGLE_API_KEY")
    if _llm is None:
        _llm = ChatGoogleGenerativeAI(
            model=model_name,
            api_key=api_key,
            google_api_key=api_key,
            temperature=0.2,
            timeout=timeout,
            max_retries=2,
        )
    return _llm
