from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
import re
from datetime import date, datetime
import httpx

RENDER_URL = "https://assistente-credito-publico.onrender.com"

app = FastAPI(
    title="Assistente Crédito Público",
    version="3.0.0",
)

# =========================
# OpenAPI (garante servers)
# =========================
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        routes=app.routes,
    )
    schema["servers"] = [{"url": RENDER_URL}]
    app.openapi_schema = schema
    return app.openapi_schema

app.openapi = custom_openapi

# =============================
# MODELS
# =============================

class AnalyzeRequest(BaseModel):
    cnpj: str
    razao_social: Optional[str] = None


class EvidenceRequest(BaseModel):
    cnpj: str
    razao_social: Optional[str] = None


class CourtAttemptRequest(BaseModel):
    cnpj: str
    uf: Optional[str] = None
    municipio: Optional[str] = None
    razao_social: Optional[str] = None


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


def years_since(iso_date: Optional[str]):
    if not iso_date:
        return None
    try:
        d = datetime.fromisoformat(iso_date).date()
    except Exception:
        # fallback para formatos comuns
        try:
            d = datetime.strptime(iso_date, "%Y-%m-%d").date()
        except Exception:
            return None

    today = date.today()
    return today.year - d.year - ((today.month, today.day) < (d.month, d.day))


# =============================
# JURISDIÇÃO (TRF / TRT)
# =============================

TRF_BY_UF = {
    # TRF1
    "AC":"TRF1","AM":"TRF1","AP":"TRF1","BA":"TRF1","DF":"TRF1","GO":"TRF1","MA":"TRF1","MT":"TRF1",
    "PA":"TRF1","PI":"TRF1","RO":"TRF1","RR":"TRF1","TO":"TRF1",
    # TRF2
    "RJ":"TRF2","ES":"TRF2",
    # TRF3
    "SP":"TRF3","MS":"TRF3",
    # TRF4
    "PR":"TRF4","SC":"TRF4","RS":"TRF4",
    # TRF5
    "AL":"TRF5","CE":"TRF5","PB":"TRF5","PE":"TRF5","RN":"TRF5","SE":"TRF5",
    # TRF6
    "MG":"TRF6",
}

TRT_BY_UF = {
    "RJ":"TRT1",
    "MG":"TRT3",
    "RS":"TRT4",
    "BA":"TRT5",
    "PE":"TRT6",
    "CE":"TRT7",
    "PA":"TRT8","AP":"TRT8",
    "PR":"TRT9",
    "DF":"TRT10","TO":"TRT10",
    "AM":"TRT11","RR":"TRT11",
    "SC":"TRT12",
    "PB":"TRT13",
    "RO":"TRT14","AC":"TRT14",
    "MA":"TRT16",
    "ES":"TRT17",
    "GO":"TRT18",
    "AL":"TRT19",
    "SE":"TRT20",
    "RN":"TRT21",
    "PI":"TRT22",
    "MT":"TRT23",
    "MS":"TRT24",
    # SP: TRT2 (Grande SP) e TRT15 (interior)
    "SP":"TRT2/TRT15",
}

def tribunal_links(uf: Optional[str], municipio: Optional[str], cnpj_digits: str, razao_social: Optional[str]):
    uf_norm = (uf or "").upper().strip() or None
    trf = TRF_BY_UF.get(uf_norm) if uf_norm else None
    trt = TRT_BY_UF.get(uf_norm) if uf_norm else None

    # Links genéricos (para navegação e fallback)
    links = {
        "cadastro_base": "https://brasilapi.com.br",
        "jusbrasil_busca": None,
        "tj_home": None,
        "trt_pje": "https://pje.trt.jus.br/consultaprocessual/",
        "trf_home": None,
    }

    q = cnpj_digits if cnpj_digits else (razao_social or "")
    if q:
        links["jusbrasil_busca"] = f"https://www.jusbrasil.com.br/busca?q={q}"

    if uf_norm:
        links["tj_home"] = f"https://www.tj{uf_norm.lower()}.jus.br"

    if trf:
        trf_links = {
            "TRF1": "https://portal.trf1.jus.br",
            "TRF2": "https://www.trf2.jus.br",
            "TRF3": "https://www.trf3.jus.br",
            "TRF4": "https://www.trf4.jus.br",
            "TRF5": "https://www.trf5.jus.br",
            "TRF6": "https://www.trf6.jus.br",
        }
        links["trf_home"] = trf_links.get(trf)

    return {"uf": uf_norm, "municipio": municipio, "trf": trf, "trt": trt, "links": links}


# =============================
# BRASILAPI - CADASTRO CNPJ
# =============================

def fetch_brasilapi_cnpj(cnpj_digits: str) -> Dict[str, Any]:
    url = f"https://brasilapi.com.br/api/cnpj/v1/{cnpj_digits}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        with httpx.Client(timeout=25, headers=headers, follow_redirects=True) as client:
            r = client.get(url)
            if r.status_code == 200:
                return {"ok": True, "source": "brasilapi_cnpj", "data": r.json()}
            return {"ok": False, "source": "brasilapi_cnpj", "status_code": r.status_code, "text": r.text[:2000]}
    except Exception as e:
        return {"ok": False, "source": "brasilapi_cnpj", "error": str(e)[:200]}


# =============================
# ENDPOINT: ANALYZE PUBLIC
# =============================

@app.post("/analyze_public")
def analyze_public(req: AnalyzeRequest):
    cnpj_digits = normalize_cnpj(req.cnpj)
    if len(cnpj_digits) != 14:
        return {"error": "cnpj_invalido", "message": "CNPJ deve ter 14 dígitos."}

    cadastro = fetch_brasilapi_cnpj(cnpj_digits)

    profile = {
        "cnpj": cnpj_digits,
        "razao_social_informada": req.razao_social,
        "razao_social_encontrada": None,
        "situacao": None,
        "data_abertura": None,
        "idade_anos": None,
        "cnae_principal": None,
        "uf": None,
        "municipio": None,
        "endereco": None,
        "fontes": [],
        "limitacoes": [],
    }

    if cadastro.get("ok"):
        d = cadastro["data"]
        profile["fontes"].append("BrasilAPI CNPJ (fonte pública)")
        profile["razao_social_encontrada"] = d.get("razao_social")
        profile["situacao"] = d.get("descricao_situacao_cadastral") or d.get("situacao_cadastral")
        profile["data_abertura"] = d.get("data_inicio_atividade")
        profile["idade_anos"] = years_since(profile["data_abertura"])
        profile["cnae_principal"] = d.get("cnae_fiscal_descricao") or d.get("cnae_fiscal")
        profile["uf"] = d.get("uf")
        profile["municipio"] = d.get("municipio")

        logradouro = d.get("logradouro") or ""
        numero = d.get("numero") or ""
        bairro = d.get("bairro") or ""
        cep = d.get("cep") or ""
        profile["endereco"] = ", ".join([x for x in [logradouro, numero, bairro, profile["municipio"], profile["uf"], cep] if x])
    else:
        profile["limitacoes"].append("Não foi possível obter cadastro via BrasilAPI (instabilidade, limite ou falha).")
        profile["fontes"].append("BrasilAPI CNPJ (falhou)")
        profile["cadastro_erro"] = cadastro

    juris = tribunal_links(profile["uf"], profile["municipio"], cnpj_digits, profile["razao_social_encontrada"] or req.razao_social)

    return {
        "perfil": profile,
        "jurisdicao": juris,
        "nota": "Fase 1: Cadastro + Jurisdição (com links para consulta)."
    }


# =============================
# ENDPOINT: EVIDENCE SEARCH (automático)
# =============================

KEY_TERMS = {
    "execucao": ["execução", "execucao"],
    "execucao_fiscal": ["execução fiscal", "execucao fiscal"],
    "protesto": ["protesto", "cartório", "cartorio"],
    "falencia": ["falência", "falencia"],
    "recuperacao_judicial": ["recuperação judicial", "recuperacao judicial"],
    "trabalhista": ["trabalhista", "reclamatória", "reclamatoria", "verbas rescisórias", "verbas rescisorias"],
    "cobranca": ["cobrança", "cobranca", "cobrança judicial", "cobranca judicial"],
}

def safe_fetch_text(url: str) -> dict:
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        with httpx.Client(timeout=20, headers=headers, follow_redirects=True) as client:
            r = client.get(url)
            if r.status_code != 200:
                return {"ok": False, "url": url, "status": r.status_code, "note": "blocked_or_error"}
            text = r.text or ""
            return {"ok": True, "url": url, "status": 200, "text": text[:200000]}
    except Exception as e:
        return {"ok": False, "url": url, "error": str(e)[:200]}

def count_terms(text: str) -> dict:
    low = (text or "").lower()
    counts = {}
    for k, variants in KEY_TERMS.items():
        counts[k] = sum(low.count(v.lower()) for v in variants)
    return counts

def top_findings(counts: dict) -> list:
    items = [(k, v) for k, v in counts.items() if v and v > 0]
    items.sort(key=lambda x: x[1], reverse=True)
    return [{"term": k, "hits": v} for k, v in items[:6]]

@app.post("/evidence_search")
def evidence_search(req: EvidenceRequest):
    cnpj_digits = normalize_cnpj(req.cnpj)
    if len(cnpj_digits) != 14:
        return {"error": "cnpj_invalido", "message": "CNPJ deve ter 14 dígitos."}

    q = cnpj_digits
    jus_url = f"https://www.jusbrasil.com.br/busca?q={q}"
    esc_url = f"https://www.escavador.com/busca?qo={q}"

    limitations = []
    sources = []

    jus = safe_fetch_text(jus_url)
    esc = safe_fetch_text(esc_url)

    combined_text = ""
    if jus.get("ok"):
        combined_text += jus.get("text", "")
        sources.append({"source": "jusbrasil", "url": jus_url})
    else:
        limitations.append({"source": "jusbrasil", "url": jus_url, "detail": jus})

    if esc.get("ok"):
        combined_text += "\n" + esc.get("text", "")
        sources.append({"source": "escavador", "url": esc_url})
    else:
        limitations.append({"source": "escavador", "url": esc_url, "detail": esc})

    counts = count_terms(combined_text)
    findings = top_findings(counts)

    signals = {
        "index_sources_ok": len(sources),
        "index_sources_blocked": len(limitations),
        "term_counts": counts,
        "top_findings": findings,
        "note": "Indícios por indexadores. NÃO confirma processos. Requer validação no tribunal."
    }

    links = [
        {"title": "JusBrasil - busca", "url": jus_url},
        {"title": "Escavador - busca", "url": esc_url},
        {"title": "Google - busca", "url": f"https://www.google.com/search?q={q}"},
    ]

    return {
        "query": q,
        "signals": signals,
        "links": links,
        "sources": sources,
        "limitations": limitations
    }


# =============================
# ENDPOINT: COURT ATTEMPT (tentativa + fallback)
# =============================

def detect_block_reason(html: str) -> Optional[str]:
    if not html:
        return None
    h = html.lower()
    if "captcha" in h or "recaptcha" in h:
        return "captcha"
    if "cloudflare" in h or "attention required" in h:
        return "cloudflare"
    if "enable javascript" in h or "javascript is required" in h:
        return "javascript_required"
    if "access denied" in h or "forbidden" in h:
        return "access_denied"
    return None

def fetch_probe(url: str) -> Dict[str, Any]:
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        with httpx.Client(timeout=20, headers=headers, follow_redirects=True) as client:
            r = client.get(url)
            text = (r.text or "")[:200000]
            reason = detect_block_reason(text)
            ok = (r.status_code == 200) and (reason is None)
            return {
                "url": url,
                "http_status": r.status_code,
                "ok": ok,
                "block_reason": reason,
            }
    except Exception as e:
        return {"url": url, "ok": False, "error": str(e)[:200]}

@app.post("/court_attempt")
def court_attempt(req: CourtAttemptRequest):
    cnpj_digits = normalize_cnpj(req.cnpj)
    if len(cnpj_digits) != 14:
        return {"error": "cnpj_invalido", "message": "CNPJ deve ter 14 dígitos."}

    uf = (req.uf or "").upper().strip() or None
    municipio = (req.municipio or "").strip() or None

    # Se UF/município não vierem, tenta derivar via BrasilAPI
    if not uf:
        cadastro = fetch_brasilapi_cnpj(cnpj_digits)
        if cadastro.get("ok"):
            d = cadastro["data"]
            uf = d.get("uf") or uf
            municipio = d.get("municipio") or municipio

    juris = tribunal_links(uf, municipio, cnpj_digits, req.razao_social)
    links = juris.get("links", {})

    tj_url = links.get("tj_home")
    trt_url = links.get("trt_pje")
    trf_url = links.get("trf_home")

    attempts: List[Dict[str, Any]] = []

    # TJ
    if tj_url:
        attempts.append({
            "source": "TJ",
            "probe": fetch_probe(tj_url),
            "manual_instructions": [
                "Abra o site do TJ.",
                "Procure por 'Consulta Processual' ou 'Consulta de Processos'.",
                "Tente pesquisar por CNPJ (se existir campo) ou por razão social.",
                "Se houver captcha/JS, a consulta precisará ser manual."
            ],
        })
    else:
        attempts.append({"source": "TJ", "probe": {"ok": False, "note": "tj_url_indisponivel"}})

    # TRT (PJe JT)
    if trt_url:
        attempts.append({
            "source": "TRT_PJe_JT",
            "probe": fetch_probe(trt_url),
            "manual_instructions": [
                "Abra o PJe - Consulta Processual.",
                "Selecione o grau (1º/2º) se solicitado.",
                "Pesquise por CNPJ e/ou razão social.",
                "Se o portal pedir captcha, registre como bloqueado e faça manualmente."
            ],
        })
    else:
        attempts.append({"source": "TRT_PJe_JT", "probe": {"ok": False, "note": "trt_url_indisponivel"}})

    # TRF
    if trf_url:
        attempts.append({
            "source": "TRF",
            "probe": fetch_probe(trf_url),
            "manual_instructions": [
                "Abra o site do TRF competente.",
                "Procure por 'Consulta Processual' (e-Proc/PJe/Consulta Pública).",
                "Pesquise por CNPJ/razão social quando disponível.",
                "Se houver captcha/JS, a consulta precisará ser manual."
            ],
        })
    else:
        attempts.append({"source": "TRF", "probe": {"ok": False, "note": "trf_url_indisponivel"}})

    return {
        "cnpj": cnpj_digits,
        "uf": uf,
        "municipio": municipio,
        "jurisdicao": {"trf": juris.get("trf"), "trt": juris.get("trt")},
        "attempts": attempts,
        "note": "Tentativa automática de acesso aos portais. ok=false + block_reason indica bloqueio (captcha/JS/etc)."
    }
