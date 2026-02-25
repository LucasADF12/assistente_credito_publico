from fastapi import FastAPI
from pydantic import BaseModel
import httpx
from bs4 import BeautifulSoup

app = FastAPI(
    title="Assistente Crédito Público",
    version="1.0.0",
    servers=[
        {"url": "https://assistente-credito-publico.onrender.com"}
    ]
)


class AnalyzeRequest(BaseModel):
    cnpj: str
    razao_social: str | None = None


@app.get("/health")
def health():
    return {"ok": True}


def fetch_text(url: str) -> str:
    # Faz download de página pública e extrai texto (simples)
    headers = {"User-Agent": "Mozilla/5.0"}
    with httpx.Client(timeout=25, follow_redirects=True, headers=headers) as client:
        r = client.get(url)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        return " ".join(soup.get_text(" ").split())


@app.post("/analyze_public")
def analyze_public(req: AnalyzeRequest):
    """
    MVP (versão 1):
    - NÃO consulta tribunais ainda (vamos adicionar depois).
    - Apenas retorna um relatório básico e um checklist, e permite evoluir.
    """
    cnpj = req.cnpj.strip()

    # Aqui você vai evoluir depois: buscar cadastro, sede, TJ/TRT/TRF, reputação etc.
    report = {
        "input": {"cnpj": cnpj, "razao_social": req.razao_social},
        "status": "MVP online",
        "next_steps": [
            "Adicionar busca de cadastro (sede/UF) em fonte pública",
            "Adicionar buscas em TJ/TRT/TRF pela UF da sede",
            "Adicionar reputação (Reclame Aqui / notícias) com links",
        ],
        "note": "Este endpoint é o começo. Ele existe para conectar no GPT Actions e provar o fluxo."
    }
    return report
