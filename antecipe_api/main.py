import csv
import io
import json
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from leitor import decodificar, ler_recebiveis, so_digitos
from precificacao import precificar
from validadores import avisos as gerar_avisos
from validadores import checar_config, fmt_cnpj, montar_contexto, prazo_dias, validar

CONFIG_PATH = Path(__file__).parent / "config_fundo.json"
FRONT_PATH = Path(__file__).parent / "antecipe_simulador.html"
SITUACAO_PATH = Path(__file__).parent / "cnpj_situacao_mock.csv"
SITUACAO_PADRAO = "ATIVA"  # CNPJ que não consta na base de situação é considerado ativo

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


def carregar_situacoes() -> dict[str, str]:
    """CNPJ (só dígitos) -> situação cadastral, lido de cnpj_situacao_mock.csv.
    Devolve {} se o arquivo não existir ou não tiver as colunas DOC e SITUACAO_CADASTRAL."""
    try:
        texto = decodificar(SITUACAO_PATH.read_bytes())
    except OSError:
        return {}
    cabecalho = texto.splitlines()[0] if texto.strip() else ""
    sep = ";" if cabecalho.count(";") >= cabecalho.count(",") else ","
    situacoes = {}
    for linha in csv.DictReader(io.StringIO(texto), delimiter=sep):
        doc = so_digitos(linha.get("DOC") or "")
        situacao = (linha.get("SITUACAO_CADASTRAL") or "").strip()
        if doc and situacao:
            situacoes[doc] = situacao
    return situacoes


@app.get("/", include_in_schema=False)
def front():
    return FileResponse(FRONT_PATH)


@app.get("/api/config")
def ver_config():
    return carregar_config()


@app.post("/api/notas/validar")
def validar_lote(
    file: UploadFile = File(...),
    data_referencia: date | None = Query(None, description="AAAA-MM-DD. Só é usada para notas sem DATA_AQUISICAO no arquivo; padrão: hoje"),
):
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(400, "Envie um arquivo .csv.")
    config = carregar_config()
    ref = data_referencia or date.today()

    try:
        itens, erros, lidas, colunas = ler_recebiveis(decodificar(file.file.read()))  # lê até o EOF
    except ValueError as e:
        raise HTTPException(422, str(e))

    # Situação cadastral: se o CSV não trouxer a coluna, usa cnpj_situacao_mock.csv.
    # Sacado que não consta nesse arquivo é considerado ATIVA.
    situacoes = carregar_situacoes() if "situacao_sacado" not in colunas else {}
    sem_registro, notas_sem_registro = set(), 0
    if situacoes:
        for r in itens:
            situacao = situacoes.get(r.sacado_cnpj)
            if not situacao:
                situacao = SITUACAO_PADRAO
                sem_registro.add(r.sacado_cnpj)
                notas_sem_registro += 1
            r.situacao_sacado = situacao
        colunas = colunas | {"situacao_sacado"}

    ctx = montar_contexto(itens, ref, colunas)  # concentração depende do lote inteiro
    avisos = gerar_avisos(config, ctx)
    if sem_registro:
        avisos.append(
            f"Situação cadastral lida de {SITUACAO_PATH.name}: {len(sem_registro)} sacado(s) "
            f"({notas_sem_registro} nota(s)) não constam no arquivo e foram considerados {SITUACAO_PADRAO}."
        )
    cnpj_fundo = so_digitos(config["fundo"]["cnpj"])
    outros = sorted({r.doc_fundo for r in itens if r.doc_fundo and r.doc_fundo != cnpj_fundo})
    if outros:
        avisos.append("O arquivo contém recebíveis de outro fundo: "
                      + ", ".join(fmt_cnpj(c) for c in outros) + ".")

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
            "cedente_nome": r.cedente_nome,
            "data_aquisicao": r.data_aquisicao.isoformat() if r.data_aquisicao else None,
            "tipo_recebivel": r.tipo_recebivel,
            "valor": float(r.valor),
            "data_vencimento": r.vencimento.isoformat(),
            "prazo_dias": prazo,
            "status": "recusada" if motivos else "aprovada",
            "motivos": motivos,
            "precificacao": preco,
        })

    contagem = Counter(m["codigo"] for n in notas for m in n["motivos"])

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
            "motivos": dict(contagem),
        },
        "notas": notas,
        "erros": erros,
        "avisos": avisos,
    }