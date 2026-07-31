from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class EditorialUnit:
    id: str
    nome: str = ''
    caminho: str = ''
    descricao: list[str] = field(default_factory=list)
    campos: list[str] = field(default_factory=list)
    botoes: list[str] = field(default_factory=list)
    parametros: list[str] = field(default_factory=list)
    opcoes: list[str] = field(default_factory=list)
    exemplos: list[str] = field(default_factory=list)
    observacoes: list[str] = field(default_factory=list)
    listas: list[list[dict[str, Any]]] = field(default_factory=list)
    destaques: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)