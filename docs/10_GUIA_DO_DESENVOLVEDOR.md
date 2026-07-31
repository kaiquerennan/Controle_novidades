# 10 - Guia do Desenvolvedor

## Como adicionar nova funcionalidade

1. Identifique se a funcionalidade e de interface, dominio ou integracao.
2. Se for UI, adicione ou ajuste um componente em `components/`.
3. Se for regra de negocio, prefira `services/` ou `models/`.
4. Se envolver prompt ou modelo, revise `core/config/settings.py` e `services/model_routing.py`.
5. Crie ou ajuste testes em `tests/`.

## Como criar novos providers

Siga o contrato de `services/providers/base_provider.py`:

- `treat_image()`;
- `suggest_orientation()`;
- `generate_novelty_plan()`;
- `review_novelty()`;
- `generate_cover()`.

Depois:

- registre o novo provider em `ProviderFactory`;
- adicione o nome em `core/config/settings.py`;
- revise validacao de modelos em `model_routing.py`.

## Como adicionar novas etapas

O fluxo principal hoje esta em `EditorialOrchestrator.generate()`.

Para incluir uma etapa:

- atualize o controle de progresso;
- inclua registro de auditoria se houver chamada externa;
- garanta tratamento de erro sanitizado;
- atualize a UI em `app.py` apenas se a etapa precisa aparecer para o usuario.

## Como criar novos componentes

Use os componentes existentes como referencia:

- `CompositionBuilder`;
- `AdvancedImageAdjuster`;
- `ImageEditor`;
- `render_novelty_preview`.

Boas praticas ja usadas no projeto:

- manter a interface separada da regra de negocio;
- preferir funcoes pequenas e focadas;
- preservar estados em `ScreenStateStore`;
- evitar carregar credenciais em arquivos persistidos;
- manter IA e exportacao desacopladas da UI.

## Boas praticas observadas no codigo

- normalize e valide antes de renderizar;
- use `safe_path()` para caminhos;
- use `ProviderError` para mensagens sanitizadas;
- mantenha os contratos de `models/` coerentes com os providers;
- cubra alteracoes com testes de regressao.

