"""Validadores configuráveis. Cada um recebe (recebível, parâmetros, contexto)
e devolve None se passou ou uma mensagem (str) se reprovou."""
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Callable

from leitor import Recebivel


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t or "")
    return "".join(c for c in t if not unicodedata.combining(c)).strip().casefold()


def brl(v) -> str:
    return "R$ " + f"{Decimal(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def pct(v) -> str:
    return f"{float(v):.1f}".replace(".", ",") + "%"


def fmt_cnpj(c: str) -> str:
    return f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}" if len(c) == 14 else c


def cnpj_valido(cnpj: str) -> bool:
    if len(cnpj) != 14 or not cnpj.isdigit() or cnpj == cnpj[0] * 14:
        return False

    def dv(base: str, pesos: list[int]) -> str:
        r = sum(int(d) * p for d, p in zip(base, pesos)) % 11
        return "0" if r < 2 else str(11 - r)

    p1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    d1 = dv(cnpj[:12], p1)
    d2 = dv(cnpj[:12] + d1, [6] + p1)
    return cnpj[12:] == d1 + d2


@dataclass
class Contexto:
    data_referencia: date
    total_lote: Decimal
    soma_sacado_raiz: dict
    soma_sacado_cnpj: dict
    soma_cedente_raiz: dict
    colunas: set


def montar_contexto(itens: list[Recebivel], data_referencia: date, colunas: set) -> Contexto:
    sr, sc, cr = defaultdict(Decimal), defaultdict(Decimal), defaultdict(Decimal)
    for r in itens:
        sr[r.sacado_cnpj[:8]] += r.valor
        sc[r.sacado_cnpj] += r.valor
        cr[r.cedente_cnpj[:8]] += r.valor
    return Contexto(data_referencia, sum((r.valor for r in itens), Decimal(0)), sr, sc, cr, colunas)


def data_base(r: Recebivel, ctx: Contexto) -> date:
    """Prazo, vencimento e desconto são sempre contados a partir da data de referência
    (hoje, por padrão). A data de aquisição do arquivo não entra no cálculo."""
    return ctx.data_referencia


def prazo_dias(r: Recebivel, ctx: Contexto) -> int:
    return (r.vencimento - data_base(r, ctx)).days


# ----------------------------------------------------------------- validadores
def cnpj_sacado_valido(r, p, ctx):
    if not cnpj_valido(r.sacado_cnpj):
        return f"CNPJ do sacado inválido: {r.sacado_cnpj or 'não informado'}."


def situacao_cadastral_sacado(r, p, ctx):
    aceitas = p.get("situacoes_aceitas", ["ATIVA"])
    if not r.situacao_sacado:
        return "Situação cadastral do sacado não informada."
    if _norm(r.situacao_sacado) not in {_norm(s) for s in aceitas}:
        return (f"Situação cadastral do sacado é {r.situacao_sacado.upper()}; "
                f"aceitas: {', '.join(aceitas)}.")


def tipo_recebivel(r, p, ctx):
    aceitos = p.get("tipos_aceitos", [])
    if _norm(r.tipo_recebivel) not in {_norm(t) for t in aceitos}:
        return f"Tipo de recebível '{r.tipo_recebivel}' não aceito; aceitos: {', '.join(aceitos)}."


def valor_minimo(r, p, ctx):
    minimo = Decimal(str(p["valor_minimo"]))
    if r.valor < minimo:
        return f"Valor {brl(r.valor)} abaixo do mínimo de {brl(minimo)}."


def nao_vencida(r, p, ctx):
    base = data_base(r, ctx)
    if r.vencimento < base:
        return (f"Nota vencida em {r.vencimento:%d/%m/%Y}, antes da data de referência "
                f"({base:%d/%m/%Y}).")


def prazo_maximo(r, p, ctx):
    limite, prazo = int(p["prazo_maximo_dias"]), prazo_dias(r, ctx)
    if prazo > limite:
        return f"Prazo acima do limite: vence em {prazo} dias; o limite configurado é {limite}."


def prazo_minimo(r, p, ctx):
    limite, prazo = int(p["prazo_minimo_dias"]), prazo_dias(r, ctx)
    if prazo < limite:
        return f"Prazo abaixo do mínimo: vence em {prazo} dias; o mínimo configurado é {limite}."


def concentracao_maxima_sacado(r, p, ctx):
    limite = Decimal(str(p["percentual_maximo"]))
    if p.get("agrupamento", "RAIZ_CNPJ") == "CNPJ":
        soma, rotulo = ctx.soma_sacado_cnpj[r.sacado_cnpj], fmt_cnpj(r.sacado_cnpj)
    else:
        soma, rotulo = ctx.soma_sacado_raiz[r.sacado_cnpj[:8]], r.sacado_cnpj[:8]
    perc = soma / ctx.total_lote * 100 if ctx.total_lote else Decimal(0)
    if perc > limite:
        return f"Sacado {rotulo} concentra {pct(perc)} do lote; o limite é {pct(limite)}."


def concentracao_maxima_cedente(r, p, ctx):
    limite = Decimal(str(p["percentual_maximo"]))
    if not r.cedente_cnpj:
        return "CNPJ do cedente não informado."
    perc = ctx.soma_cedente_raiz[r.cedente_cnpj[:8]] / ctx.total_lote * 100
    if perc > limite:
        return f"Cedente {r.cedente_cnpj[:8]} concentra {pct(perc)} do lote; o limite é {pct(limite)}."


REGISTRO: dict[str, Callable] = {
    "CNPJ_SACADO_VALIDO": cnpj_sacado_valido,
    "SITUACAO_CADASTRAL_SACADO": situacao_cadastral_sacado,
    "TIPO_RECEBIVEL": tipo_recebivel,
    "VALOR_MINIMO": valor_minimo,
    "NAO_VENCIDA": nao_vencida,
    "PRAZO_MAXIMO": prazo_maximo,
    "PRAZO_MINIMO": prazo_minimo,
    "CONCENTRACAO_MAXIMA_SACADO": concentracao_maxima_sacado,
    "CONCENTRACAO_MAXIMA_CEDENTE": concentracao_maxima_cedente,
}


# validadores que só fazem sentido se o arquivo trouxer a coluna correspondente
EXIGE_COLUNA = {
    "SITUACAO_CADASTRAL_SACADO": ("situacao_sacado", "situação cadastral do sacado"),
    "CONCENTRACAO_MAXIMA_CEDENTE": ("cedente_cnpj", "CNPJ do cedente"),
}


def _sem_coluna(codigo: str, ctx: Contexto) -> str | None:
    exige = EXIGE_COLUNA.get(codigo)
    return exige[1] if exige and exige[0] not in ctx.colunas else None


def avisos(config: dict, ctx: Contexto) -> list[str]:
    """Validadores ativos que não puderam ser aplicados por falta de coluna no arquivo."""
    return [
        f"{v['codigo']} não foi aplicado: o arquivo não tem a coluna de {_sem_coluna(v['codigo'], ctx)}. "
        "As notas não foram verificadas nesse critério."
        for v in config["validadores"]
        if v.get("ativo") and _sem_coluna(v["codigo"], ctx)
    ]


def checar_config(config: dict) -> None:
    desconhecidos = [v["codigo"] for v in config.get("validadores", [])
                     if v.get("ativo") and v["codigo"] not in REGISTRO]
    if desconhecidos:
        raise ValueError(f"Validadores sem implementação: {', '.join(desconhecidos)}")


def validar(r: Recebivel, config: dict, ctx: Contexto) -> list[dict]:
    """Roda TODOS os validadores ativos (não para no primeiro erro)."""
    motivos = []
    for v in config["validadores"]:
        if not v.get("ativo") or _sem_coluna(v["codigo"], ctx):
            continue
        msg = REGISTRO[v["codigo"]](r, v.get("parametros", {}), ctx)
        if msg:
            motivos.append({"codigo": v["codigo"], "mensagem": msg})
    return motivos