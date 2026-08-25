from langchain_core.prompts import ChatPromptTemplate
from app.ai import get_llm
from app.ai.rag import retrieve_context
from app.extensions import get_supabase

_STUDENT_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "You are TalentForge's assistant for students. Answer only using the context below: the "
     "student's validated skills, weaknesses, open postings, and available courses. If the answer "
     "isn't in the context, say you don't have that information yet. Keep answers to 2-3 sentences."),
    ("human", "Context:\n{context}\n\nQuestion:\n{question}"),
])

_INDUSTRY_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "You are TalentForge's assistant for industry recruiters. Answer only using the context below: "
     "their job postings, courses, and applicant funnel. If the answer isn't in the context, say you "
     "don't have that information yet. Keep answers to 2-3 sentences."),
    ("human", "Context:\n{context}\n\nQuestion:\n{question}"),
])


def answer_student_question(context, question, owner_id):
    llm = get_llm()
    chain = _STUDENT_PROMPT | llm
    response = chain.invoke({"context": retrieve_context(owner_id, question, context, get_supabase()), "question": question})
    return response.content


def answer_industry_question(context, question, owner_id):
    llm = get_llm()
    chain = _INDUSTRY_PROMPT | llm
    response = chain.invoke({"context": retrieve_context(owner_id, question, context, get_supabase()), "question": question})
    return response.content
