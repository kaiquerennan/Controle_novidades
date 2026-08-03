from __future__ import annotations

from models.composition import CompositionDocument, ContentBlock


LIST_MARKER = '- '
TEXT_TYPES = frozenset({'titulo', 'subtitulo', 'texto', 'lista'})


def composition_to_plain_text(document: CompositionDocument) -> str:
    """Converte a matéria em texto corrido, como ela seria lida.

    Blocos de imagem e divisores não aparecem: eles são posicionados na
    pré-visualização, não no texto.
    """
    paragrafos: list[str] = []
    for block in document.blocks:
        if block.tipo in {'titulo', 'subtitulo', 'texto'}:
            conteudo = block.conteudo.strip()
            if conteudo:
                paragrafos.append(conteudo)
        elif block.tipo == 'lista':
            linhas = [
                f'{LIST_MARKER}{str(item.get("texto") or "").strip()}'
                for item in block.itens
                if str(item.get('texto') or '').strip()
            ]
            if linhas:
                paragrafos.append('\n'.join(linhas))
    return '\n\n'.join(paragrafos)


def _blocks_from_plain_text(text: str) -> list[ContentBlock]:
    normalizado = str(text or '').replace('\r\n', '\n').replace('\r', '\n')
    paragrafos = [
        bloco.strip() for bloco in normalizado.split('\n\n') if bloco.strip()
    ]
    blocos: list[ContentBlock] = []
    for indice, paragrafo in enumerate(paragrafos):
        linhas = [linha.strip() for linha in paragrafo.splitlines() if linha.strip()]
        if not linhas:
            continue
        if indice == 0:
            blocos.append(ContentBlock(tipo='titulo', conteudo=' '.join(linhas)))
            continue
        if all(linha.startswith(('-', '•', '*')) for linha in linhas):
            itens = [{'texto': linha.lstrip('-•* ').strip()} for linha in linhas]
            blocos.append(ContentBlock(tipo='lista', itens=[i for i in itens if i['texto']]))
            continue
        blocos.append(ContentBlock(tipo='texto', conteudo='\n'.join(linhas)))
    primeiro_texto = next((b for b in blocos if b.tipo == 'texto'), None)
    if primeiro_texto is not None:
        primeiro_texto.papel = 'introducao'
    return blocos


def apply_plain_text(document: CompositionDocument, text: str) -> None:
    """Reescreve a matéria a partir do texto corrido, preservando as imagens.

    Cada bloco de imagem é reinserido depois da mesma quantidade de parágrafos
    que o precedia antes da edição. Assim as imagens acompanham o texto sem
    precisar de marcadores no meio da escrita.
    """
    ancoras: list[tuple[int, ContentBlock]] = []
    paragrafos_antes = 0
    for block in document.blocks:
        if block.tipo in TEXT_TYPES:
            paragrafos_antes += 1
        else:
            ancoras.append((paragrafos_antes, block))

    novos = _blocks_from_plain_text(text)
    # Insere em ordem crescente somando os já inseridos, senão cada inserção
    # desloca as âncoras seguintes.
    for inseridos, (posicao, bloco) in enumerate(sorted(ancoras, key=lambda par: par[0])):
        novos.insert(min(posicao + inseridos, len(novos)), bloco)

    document.blocks = novos
    document.source = 'manual'


__all__ = ['LIST_MARKER', 'apply_plain_text', 'composition_to_plain_text']
