from fastapi import FastAPI
from pydantic import BaseModel
import re
from datetime import date, datetime
import httpx

RENDER_URL = "https://assistente-credito-publico.onrender.com"

app = FastAPI(
    title="Assistente Crédito Público",
    version="1.0.0",
    servers=[{"url": RENDER_URL}],
)

class AnalyzeRequest(BaseModel):
    cnpj: str
    razao_social: str | None = None

@app.get("/health")
def health():
    return {"ok": True}

def normalize_cnpj(cnpj: str) -> str:
    return re.sub(r"\D", "", cnpj or "")

def years_since(iso_date: str | None) -> int | None:
    if not iso_date:
        return None
    try:
        d = datetime.fromisoformat(iso_date).date()
    except Exception:
        try:
            d = datetime.strptime(iso_date, "%Y-%m-%d").date()
        except Exception:
            return None
    today = date.today()
    return today.year - d.year - ((today.month, today.day) < (d.month, d.day))

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
    # SP é especial: TRT2 (Grande SP/litoral) e TRT15 (interior).
    "SP":"TRT2/TRT15",
}

def tribunal_links(uf: str | None, municipio: str | None, cnpj_digits: str, razao_social: str | None):
    uf = (uf or "").upper().strip() or None
    trf = TRF_BY_UF.get(uf) if uf else None
    trt = TRT_BY_UF.get(uf) if uf else None

    # Links genéricos (não garantem consulta por CNPJ em todos os sites; servem como “atalhos”)
    links = {
        "cadastro_base": "https://brasilapi.com.br",
        "jusbrasil_busca": None,
        "tj_busca": None,
        "trt_busca": None,
        "trf_busca": None,
    }

    # JusBrasil (busca)
    q = cnpj_digits if cnpj_digits else (razao_social or "")
    if q:
        links["jusbrasil_busca"] = f"https://www.jusbrasil.com.br/busca?q={q}"

    # TJ por UF (mapeamento simples; você pode refinar depois)
    if uf:
        links["tj_busca"] = f"https://www.tj{uf.lower()}.jus.br"

    # TRT / PJe JT (link genérico)
    if trt:
        links["trt_busca"] = "https://pje.trt.jus.br/consultaprocessual/"

    # TRF (links genéricos por região)
    if trf:
        trf_links = {
            "TRF1": "https://portal.trf1.jus.br",
            "TRF2": "https://www.trf2.jus.br",
            "TRF3": "https://www.trf3.jus.br",
            "TRF4": "https://www.trf4.jus.br",
            "TRF5": "https://www.trf5.jus.br",
            "TRF6": "https://www.trf6.jus.br",
        }
        links["trf_busca"] = trf_links.get(trf)

    return {"uf": uf, "municipio": municipio, "trf": trf, "trt": trt, "links": links}

def fetch_brasilapi_cnpj(cnpj_digits: str) -> dict:
    """
    Fonte pública: BrasilAPI (não é 'Receita oficial', mas é aberta e útil para cadastro inicial).
    """
    url = f"https://brasilapi.com.br/api/cnpj/v1/{cnpj_digits}"
    headers = {"User-Agent": "Mozilla/5.0"}
    with httpx.Client(timeout=25, headers=headers, follow_redirects=True) as client:
        r = client.get(url)
        if r.status_code == 200:
            return {"ok": True, "source": "brasilapi_cnpj", "data": r.json()}
        return {"ok": False, "source": "brasilapi_cnpj", "status_code": r.status_code, "text": r.text[:4000]}

@app.post("/analyze_public")
def analyze_public(req: AnalyzeRequest):
    cnpj_digits = normalize_cnpj(req.cnpj)
    if len(cnpj_digits) != 14:
        return {"error": "cnpj_invalido", "message": "CNPJ deve ter 14 dígitos (com ou sem pontuação)."}

    # 1) Cadastro básico via fonte pública
    cadastro = fetch_brasilapi_cnpj(cnpj_digits)

    # 2) Extrair sede/UF/município e dados úteis
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
        "limitações": [],
    }

    if cadastro.get("ok"):
        d = cadastro["data"]
        profile["fontes"].append("BrasilAPI CNPJ (fonte pública)")
        profile["razao_social_encontrada"] = d.get("razao_social")
        profile["situacao"] = d.get("descricao_situacao_cadastral") or d.get("situacao_cadastral")
        profile["data_abertura"] = d.get("data_inicio_atividade")
        profile["idade_anos"] = years_since(profile["data_abertura"])
        profile["cnae_principal"] = (d.get("cnae_fiscal_descricao") or d.get("cnae_fiscal"))
        profile["uf"] = d.get("uf")
        profile["municipio"] = d.get("municipio")
        logradouro = d.get("logradouro") or ""
        numero = d.get("numero") or ""
        bairro = d.get("bairro") or ""
        cep = d.get("cep") or ""
        profile["endereco"] = ", ".join([x for x in [logradouro, numero, bairro, profile["municipio"], profile["uf"], cep] if x])
    else:
        profile["limitações"].append("Não foi possível obter cadastro via BrasilAPI (pode ser instabilidade ou limite).")
        profile["fontes"].append("BrasilAPI CNPJ (falhou)")
        profile["cadastro_erro"] = cadastro

    # 3) Jurisdição + links práticos
    juris = tribunal_links(profile["uf"], profile["municipio"], cnpj_digits, profile["razao_social_encontrada"] or req.razao_social)

    # 4) Saída “pronta para agente”
    resultado = {
        "perfil": profile,
        "jurisdicao": juris,
        "proximos_passos_recomendados": [
            "Consultar TJ do estado da sede (site do TJ) por CNPJ e razão social (se o portal permitir).",
            "Consultar PJe Justiça do Trabalho (consultaprocessual) por CNPJ/razão social.",
            "Consultar TRF competente (site do TRF) e buscar por execuções fiscais/ações federais (quando disponível).",
            "Validar também em indexadores públicos (JusBrasil/Escavador) e filtrar homônimos.",
        ],
        "nota": "Esta é a Fase 1 (cadastro + jurisdição). A Fase 2 adiciona varredura automática de TJ/TRT/TRF via fontes públicas e indexadores."
    }
    return resultado
