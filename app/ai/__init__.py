from flask import current_app
from langchain_google_genai import ChatGoogleGenerativeAI

_llm = None


def get_llm():
    global _llm
    if _llm is None:
        _llm = ChatGoogleGenerativeAI(
            model=current_app.config["GEMINI_MODEL"],
            google_api_key=current_app.config["GOOGLE_API_KEY"],
            temperature=0.2,
        )
    return _llm
