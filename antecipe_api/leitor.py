"""Leitura do CSV de recebíveis, linha a linha, até o EOF."""
import csv
import io
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

# nome canônico -> nomes aceitos no cabeçalho (já normalizados: minúsculo, sem acento)
ALIASES = {
    "nota": ["nota", "nu_documento", "numero_documento", "documento"],
    "sacado_cnpj": ["sacado_cnpj", "doc_sacado", "cnpj_sacado"],
    "sacado_nome": ["sacado_nome", "nome_sacado", "sacado"],
    "cedente_cnpj": ["cedente_cnpj", "doc_cedente", "cnpj_cedente"],
    "cedente_nome": ["cedente_nome", "nome_cedente", "cedente"],
    "tipo_recebivel": ["tipo_recebivel", "tipo"],
    "valor": ["valor", "valor_nominal"],
    "data_vencimento": ["data_vencimento", "data_vencimento_original", "vencimento"],
    "data_aquisicao": ["data_aquisicao", "data_cessao"],
    "situacao_sacado": ["situacao_sacado", "situacao_cadastral_sacado", "situacao_cadastral"],
    "doc_fundo": ["doc_fundo", "cnpj_fundo"],
}
OBRIGATORIAS = {"nota", "sacado_cnpj", "tipo_recebivel", "valor", "data_vencimento"}
_REVERSO = {alias: canon for canon, nomes in ALIASES.items() for alias in nomes}


@dataclass
class Recebivel:
    linha: int
    nota: str
    sacado_cnpj: str  # somente dígitos
    sacado_nome: str
    cedente_cnpj: str  # somente dígitos ("" se não informado)
    cedente_nome: str
    tipo_recebivel: str
    valor: Decimal
    vencimento: date
    data_aquisicao: date | None
    situacao_sacado: str
    doc_fundo: str


def decodificar(bruto: bytes) -> str:
    try:
        return bruto.decode("utf-8-sig")
    except UnicodeDecodeError:  # CSV do Excel (Windows-1252)
        return bruto.decode("cp1252", errors="replace")


def _coluna(nome: str) -> str:
    s = unicodedata.normalize("NFKD", nome)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", "_", s.strip().lower())


def parse_valor(texto: str) -> Decimal:
    """Aceita '10000', '10.000,50', 'R$ 10.000', '10000.50', '10.000'."""
    s = re.sub(r"[^\d,.\-]", "", texto)
    if "," in s:  # padrão brasileiro
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"-?\d{1,3}(\.\d{3})+", s):  # '10.000' = dez mil
        s = s.replace(".", "")
    try:
        return Decimal(s)
    except InvalidOperation:
        raise ValueError(f"valor inválido: {texto!r}")


def parse_data(texto: str, rotulo: str = "data") -> date:
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(texto.strip()[:10], fmt).date()
        except ValueError:
            pass
    raise ValueError(f"{rotulo} inválida: {texto!r} (use AAAA-MM-DD ou DD/MM/AAAA)")


def so_digitos(texto: str) -> str:
    return re.sub(r"\D", "", texto or "")


def ler_recebiveis(texto: str) -> tuple[list[Recebivel], list[dict], int, set[str]]:
    """Retorna (recebíveis, erros por linha, linhas de dados lidas, colunas presentes)."""
    fluxo = io.StringIO(texto, newline="")
    cabecalho = fluxo.readline()
    if not cabecalho.strip():
        raise ValueError("O arquivo está vazio.")

    sep = ";" if cabecalho.count(";") > cabecalho.count(",") else ","
    brutos = next(csv.reader([cabecalho], delimiter=sep))
    nomes = [_REVERSO.get(_coluna(c), _coluna(c)) for c in brutos]
    faltando = OBRIGATORIAS - set(nomes)
    if faltando:
        detalhe = "; ".join(f"{c} (aceita: {', '.join(ALIASES[c])})" for c in sorted(faltando))
        raise ValueError(f"Colunas ausentes: {detalhe}.")

    itens: list[Recebivel] = []
    erros: list[dict] = []
    lidas = 0
    for num, linha in enumerate(csv.reader(fluxo, delimiter=sep), start=2):  # até o EOF
        if not any(c.strip() for c in linha):
            continue
        lidas += 1
        reg = dict(zip(nomes, (c.strip() for c in linha)))
        try:
            if not reg.get("nota"):
                raise ValueError("número da nota não informado")
            aquisicao = reg.get("data_aquisicao", "")
            itens.append(
                Recebivel(
                    linha=num,
                    nota=reg["nota"],
                    sacado_cnpj=so_digitos(reg.get("sacado_cnpj", "")),
                    sacado_nome=reg.get("sacado_nome", ""),
                    cedente_cnpj=so_digitos(reg.get("cedente_cnpj", "")),
                    cedente_nome=reg.get("cedente_nome", ""),
                    tipo_recebivel=reg.get("tipo_recebivel", ""),
                    valor=parse_valor(reg.get("valor", "")),
                    vencimento=parse_data(reg.get("data_vencimento", ""), "data de vencimento"),
                    data_aquisicao=parse_data(aquisicao, "data de aquisição") if aquisicao else None,
                    situacao_sacado=reg.get("situacao_sacado", ""),
                    doc_fundo=so_digitos(reg.get("doc_fundo", "")),
                )
            )
        except ValueError as e:
            erros.append({"linha": num, "mensagem": str(e)})
    return itens, erros, lidas, set(nomes)