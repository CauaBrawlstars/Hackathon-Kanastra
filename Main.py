import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from leitor import decodificar, ler_recebiveis
from precificacao import precificar
from validadores import checar_config, fmt_cnpj, montar_contexto, prazo_dias, validar

CONFIG_PATH = Path(__file__).parent / "config_fundo.json"
FRONT_PATH = Path(__file__).parent / "antecipe_simulador.html"

app = FastAPI(title="Antecipe API", description="Validação de lotes de recebíveis")
app.add_middleware(  # front-end em outra porta/origem; restrinja em produção
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


def carregar_config() -> dict:
    """Relido a cada requisição: alterar o JSON não exige reiniciar o servidor."""
    try:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        checar_config(config)
        return config
    except (OSError, ValueError, KeyError) as e:
        raise HTTPException(500, f"Configuração do fundo inválida: {e}")


@app.get("/", include_in_schema=False)
def front():
    return FileResponse(FRONT_PATH)


@app.get("/api/config")
def ver_config():
    return carregar_config()


@app.post("/api/notas/validar")
async def validar_lote(
    file: UploadFile = File(...),
    data_referencia: date | None = Query(None, description="AAAA-MM-DD; padrão: hoje"),
):
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(400, "Envie um arquivo .csv.")
    config = carregar_config()
    ref = data_referencia or date.today()

    try:
        itens, erros, lidas = ler_recebiveis(decodificar(await file.read()))  # lê até o EOF
    except ValueError as e:
        raise HTTPException(422, str(e))

    ctx = montar_contexto(itens, ref)  # concentração depende do lote inteiro
    notas, aprovadas = [], []
    for r in itens:
        motivos = validar(r, config, ctx)
        prazo = prazo_dias(r, ctx)
        preco = None
        if not motivos:
            preco = precificar(r, prazo, config["precificacao"])
            aprovadas.append((r, preco))
        notas.append({
            "linha": r.linha,
            "nota": r.nota,
            "sacado_cnpj": fmt_cnpj(r.sacado_cnpj),
            "sacado_nome": r.sacado_nome,
            "cedente_cnpj": fmt_cnpj(r.cedente_cnpj),
            "tipo_recebivel": r.tipo_recebivel,
            "valor": float(r.valor),
            "data_vencimento": r.vencimento.isoformat(),
            "prazo_dias": prazo,
            "status": "recusada" if motivos else "aprovada",
            "motivos": motivos,
            "precificacao": preco,
        })

    return {
        "fundo": config["fundo"],
        "arquivo": file.filename,
        "data_referencia": ref.isoformat(),
        "resumo": {
            "linhas_lidas": lidas,
            "linhas_com_erro": len(erros),
            "total_notas": len(notas),
            "aprovadas": len(aprovadas),
            "recusadas": len(notas) - len(aprovadas),
            "valor_total": float(ctx.total_lote),
            "valor_aprovado": float(sum((r.valor for r, _ in aprovadas), Decimal(0))),
            "valor_presente_aprovado": round(sum(p["valor_presente"] for _, p in aprovadas), 2),
        },
        "notas": notas,
        "erros": erros,
    }