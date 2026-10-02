"""Design System HUMIAT — contrato visual compartilhável entre os produtos.

A estrutura/tamanhos ficam no design system. A empresa altera somente as cores de
identidade. Quando não existe identidade SolVoz, o tema HUMIAT é o fallback.
"""
from __future__ import annotations

import re
from typing import Mapping

HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

HUMIAT_DEFAULT_THEME = {
    "tema": "humiat",
    "source": "humiat",
    "company_slug": "",
    "brand": "#0B74FF",
    "brand_2": "#00AFC8",
    "accent": "#845CFF",
    # Guardadas por compatibilidade com a paleta de 6 cores do SolVoz.
    # O Organiza não pinta o background inteiro com a marca: bg/surface/text
    # continuam disponíveis para os outros produtos e futuras evoluções.
    "bg": "#F5F7FB",
    "surface": "#FFFFFF",
    "text": "#172033",
}


def _hex(value: object, fallback: str) -> str:
    raw = str(value or "").strip()
    return raw.upper() if HEX_RE.fullmatch(raw) else fallback.upper()


def normalize_theme(data: Mapping[str, object] | None = None, *, source: str = "humiat", company_slug: str = "") -> dict:
    base = dict(HUMIAT_DEFAULT_THEME)
    data = dict(data or {})
    source_n = (source or "humiat").strip().lower()
    slug_n = (company_slug or "").strip().lower()
    if source_n == "solvoz":
        base.update({
            "tema": str(data.get("tema") or "solvoz").strip() or "solvoz",
            "source": "solvoz",
            "company_slug": slug_n,
            "brand": _hex(data.get("brand"), base["brand"]),
            "brand_2": _hex(data.get("brand_2"), base["brand_2"]),
            "accent": _hex(data.get("accent"), base["accent"]),
            "bg": _hex(data.get("bg"), base["bg"]),
            "surface": _hex(data.get("surface"), base["surface"]),
            "text": _hex(data.get("text"), base["text"]),
        })
    else:
        base["source"] = "humiat"
        base["company_slug"] = slug_n

    base["class_name"] = "theme-solvoz" if base["source"] == "solvoz" else "theme-humiat"
    # Somente valores validados entram no style inline.
    base["style"] = ";".join([
        f"--h-primary:{base['brand']}",
        f"--h-secondary:{base['brand_2']}",
        f"--h-accent:{base['accent']}",
    ])
    base["primary"] = base["brand"]
    return base
