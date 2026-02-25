from fastapi import FastAPI
from pydantic import BaseModel
import re
from datetime import date, datetime
import httpx

RENDER_URL = "https://assistente-credito-publico.onrender.com"

app = FastAPI(
    title="Assistente Crédito Público",
    version="2.0.0",
    servers=[{"url": RENDER_URL}],
)

# =============================
# MODELS
# =============================

class AnalyzeRequest(BaseModel):
    cnpj: str
    razao_social: str | None = None


class EvidenceRequest(BaseModel):
    cnpj: str
    razao_social: str | None = None


# =============================
# HEALTH
# =============================

@app.get("/health")
def health():
    return {"ok": True}


# =============================
# HELPERS
# =============================

def normalize_cnpj(cnpj: str) -> str:
    return re.sub(r"\D", "", cnpj or "")


def years_since(iso_date: str | None):
    if not iso_date:
        return None
    try:
        d = datetime.fromisoformat(iso_date).date()
    except:
        return None

    today = date.today()
    return today.year - d.year - (
        (today.month, today.day) < (d.month, d.day)
    )


# =============================
# JURISDIÇÃO
# =============================

TRF_BY_UF = {
    "MG": "TRF6",
    "SP": "TRF3",
    "RJ": "TRF2",
    "ES": "TRF2",
    "PR": "TRF4",
    "SC": "TRF4",
    "RS": "TRF4",
}

TRT_BY_UF = {
    "MG": "TRT3",
    "RJ": "TRT1",
    "RS": "TRT4",
    "PR": "TRT9",
    "SC": "TRT12",
    "SP": "TRT2/TRT15",
}


def tribunal_links(uf, municipio, cnpj):
    return {
        "uf": uf,
        "municipio": municipio,
        "trf": TRF_BY_UF.get(uf),
        "trt": TRT_BY_UF.get(uf),
        "links": {
            "tj": f"https://www.tj{uf.lower()}.jus.br" if uf else None,
            "trt": "https://pje.trt.jus.br/consultaprocessual/",
            "jusbrasil": f"https://www.jusbrasil.com.br/busca?q={cnpj}",
        },
    }


# =============================
# CONSULTA CNPJ (BRASILAPI)
# =============================

def fetch_brasilapi(cnpj):
    url = f"https://brasilapi.com.br/api/cnpj/v1/{cnpj}"

    try:
        with httpx.Client(timeout=20) as client:
            r = client.get(url)

            if r.status_code == 200:
                return r.json()

    except:
        pass

    return None


# =============================
# ANALYZE PUBLIC
# =============================

@app.post("/analyze_public")
def analyze_public(req: AnalyzeRequest):

    cnpj = normalize_cnpj(req.cnpj)

    if len(cnpj) != 14:
        return {"error": "cnpj_invalido"}

    data = fetch_brasilapi(cnpj)

    if not data:
        return {"error": "cadastro_indisponivel"}

    abertura = data.get("data_inicio_atividade")

    profile = {
        "cnpj": cnpj,
        "razao_social": data.get("razao_social"),
        "situacao": data.get("descricao_situacao_cadastral"),
        "data_abertura": abertura,
        "idade_anos": years_since(abertura),
        "cnae": data.get("cnae_fiscal_descricao"),
        "uf": data.get("uf"),
        "municipio": data.get("municipio"),
        "endereco": f"{data.get('logradouro')} {data.get('numero')} - {data.get('bairro')}",
    }

    jurisdicao = tribunal_links(
        profile["uf"],
        profile["municipio"],
        cnpj
    )

    return {
        "perfil": profile,
        "jurisdicao": jurisdicao,
        "nota": "Cadastro público obtido via BrasilAPI"
    }


# =============================
# EVIDENCE SEARCH AUTOMÁTICO
# =============================

KEY_TERMS = {
    "execucao": ["execução", "execucao"],
    "execucao_fiscal": ["execução fiscal"],
    "protesto": ["protesto"],
    "falencia": ["falência", "falencia"],
    "recuperacao_judicial": ["recuperação judicial"],
    "trabalhista": ["trabalhista", "reclamatória"],
    "cobranca": ["cobrança", "cobranca"],
}


def safe_fetch(url):
    try:
        with httpx.Client(
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0"},
            follow_redirects=True,
        ) as client:

            r = client.get(url)

            if r.status_code != 200:
                return None

            return r.text.lower()[:200000]

    except:
        return None


def count_terms(text):
    results = {}

    if not text:
        return results

    for key, variants in KEY_TERMS.items():
        results[key] = sum(text.count(v) for v in variants)

    return results


@app.post("/evidence_search")
def evidence_search(req: EvidenceRequest):

    cnpj = normalize_cnpj(req.cnpj)

    jus_url = f"https://www.jusbrasil.com.br/busca?q={cnpj}"
    esc_url = f"https://www.escavador.com/busca?qo={cnpj}"

    jus_text = safe_fetch(jus_url)
    esc_text = safe_fetch(esc_url)

    combined = (jus_text or "") + (esc_text or "")

    counts = count_terms(combined)

    findings = [
        {"term": k, "hits": v}
        for k, v in counts.items()
        if v > 0
    ]

    findings.sort(key=lambda x: x["hits"], reverse=True)

    signals = {
        "term_counts": counts,
        "top_findings": findings[:5],
        "note": "Indícios baseados em indexadores públicos"
    }

    return {
        "query": cnpj,
        "signals": signals,
        "links": [
            {"title": "JusBrasil", "url": jus_url},
            {"title": "Escavador", "url": esc_url},
            {"title": "Google", "url": f"https://google.com/search?q={cnpj}"}
        ]
    }
