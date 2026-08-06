from __future__ import annotations

from typing import Any, Callable

from nicegui import ui


def render_date_field(
    label: str,
    on_change: Callable[[str], Any],
    *,
    mark: str = '',
) -> Any:
    """Entrada de data em DD/MM/AAAA, digitável ou pelo calendário no ícone.

    A máscara garante o formato ao digitar, e o debounce evita uma consulta a
    cada tecla: quem chama só recebe o valor quando a digitação para.
    """
    entrada = ui.input(label, placeholder='dd/mm/aaaa').props(
        'outlined dense clearable debounce=500 mask=##/##/#### '
        'style="width: 9.5rem"'
    )
    if mark:
        entrada.mark(mark)
    with entrada:
        with ui.menu().props('no-parent-event') as calendario:
            ui.date(mask='DD/MM/YYYY').bind_value(entrada)
            with ui.row().classes('w-full justify-end p-2'):
                ui.button('Fechar', on_click=calendario.close).props('flat dense')
        with entrada.add_slot('append'):
            ui.icon('event').classes('cursor-pointer').on('click', calendario.open)
    entrada.on_value_change(lambda event: on_change(str(event.value or '')))
    return entrada


__all__ = ['render_date_field']
