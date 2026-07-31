from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4


DATA_TYPES = (
    'Cliente', 'Fornecedor', 'Nome', 'Empresa', 'CPF/CNPJ', 'Telefone',
    'E-mail', 'Endereço', 'Texto personalizado',
)


@dataclass(slots=True)
class AnonymizationArea:
    """Area manual cuja coordenada e uma fracao da imagem (0 a 1)."""

    points: list[tuple[float, float]]
    data_type: str = 'Texto personalizado'
    replacement_text: str = ''
    note: str = ''
    id: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self) -> None:
        if len(self.points) < 2:
            raise ValueError('A area de anonimizacao precisa de dois pontos.')
        self.points = [
            (min(1.0, max(0.0, float(x))), min(1.0, max(0.0, float(y))))
            for x, y in self.points[:2]
        ]
        if self.data_type not in DATA_TYPES:
            self.data_type = 'Texto personalizado'
        self.replacement_text = str(self.replacement_text or '').strip()
        self.note = str(self.note or '').strip()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> 'AnonymizationArea':
        return cls(
            points=list(value.get('points') or []),
            data_type=str(value.get('data_type') or 'Texto personalizado'),
            replacement_text=str(value.get('replacement_text') or ''),
            note=str(value.get('note') or ''),
            id=str(value.get('id') or uuid4().hex),
        )
