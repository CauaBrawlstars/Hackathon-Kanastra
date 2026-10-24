from decimal import ROUND_HALF_UP, Decimal

from leitor import Recebivel

CENTAVOS = Decimal("0.01")


def precificar(r: Recebivel, prazo: int, cfg: dict) -> dict:
    """DESCONTO_SIMPLES (comercial): desconto = valor × taxa_mensal × prazo / base_dias."""
    if cfg.get("metodo") != "DESCONTO_SIMPLES":
        raise ValueError(f"Método de precificação não suportado: {cfg.get('metodo')}")
    taxa = Decimal(str(cfg["taxa_mensal_percentual"])) / 100
    base = Decimal(str(cfg["base_dias"]))
    desconto = (r.valor * taxa * Decimal(prazo) / base).quantize(CENTAVOS, ROUND_HALF_UP)
    return {
        "prazo_dias": prazo,
        "desconto": float(desconto),
        "valor_presente": float(r.valor - desconto),
    }
